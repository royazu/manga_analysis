from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from etl import (
    PROCESSED_DIR,
    RAW_DIR,
    data_preprocessing,
    etl_processing,
    genre_success_stats,
    model_training,
)

st.set_page_config(
    page_title="Manga Analysis Dashboard",
    page_icon="📚",
    layout="wide",
)

RAW_PATH = RAW_DIR / "top_1000_manga.json"
GENRE_PATH = PROCESSED_DIR / "genre_success_stats.csv"


def _stringify_cell(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def display_frame(df: pd.DataFrame, max_rows: int = 200) -> pd.DataFrame:
    """Make nested cells Streamlit-friendly."""
    shown = df.head(max_rows).copy()
    for col in shown.columns:
        if shown[col].dtype == object:
            shown[col] = shown[col].map(_stringify_cell)
    return shown


@st.cache_data(show_spinner="Loading raw manga data...")
def load_raw_data(_mtime: float) -> pd.DataFrame:
    if not RAW_PATH.exists():
        raise FileNotFoundError(
            f"Missing {RAW_PATH}. Run `python etl.py` once to fetch the dataset."
        )
    with RAW_PATH.open(encoding="utf-8") as f:
        return pd.DataFrame(json.load(f))


@st.cache_data(show_spinner="Processing data...")
def load_processed_data(_mtime: float) -> pd.DataFrame:
    raw_df = load_raw_data(_mtime)
    return etl_processing(data_preprocessing(raw_df))


@st.cache_data(show_spinner="Running K-Fold model evaluation...")
def load_model_results(_mtime: float):
    processed_df = load_processed_data(_mtime)
    _, y_true, y_pred, metrics = model_training(processed_df)
    # Metrics contains numpy/pandas objects; keep only serializable pieces + frames.
    return {
        "y_true": y_true.astype(str).tolist(),
        "y_pred": list(y_pred),
        "accuracy": metrics["accuracy"],
        "precision": metrics["precision"],
        "recall": metrics["recall"],
        "f1": metrics["f1"],
        "roc_auc": metrics["roc_auc"],
        "classification_report": metrics["classification_report"],
        "classification_report_dict": metrics["classification_report_dict"],
        "confusion_matrix": metrics["confusion_matrix"].tolist(),
        "labels": metrics["labels"],
        "group_importance": metrics["group_importance"].to_dict(),
        "most_contributing_field": metrics["most_contributing_field"],
    }


@st.cache_data(show_spinner="Building genre success stats...")
def load_genre_stats(_mtime: float) -> pd.DataFrame:
    if GENRE_PATH.exists():
        return pd.read_csv(GENRE_PATH)
    processed_df = load_processed_data(_mtime)
    genre_df = genre_success_stats(processed_df)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    genre_df.to_csv(GENRE_PATH, index=False)
    return genre_df


def raw_mtime() -> float:
    return RAW_PATH.stat().st_mtime if RAW_PATH.exists() else 0.0


def render_overview(raw_df: pd.DataFrame, processed_df: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Raw rows", f"{len(raw_df):,}")
    c2.metric("Processed rows", f"{len(processed_df):,}")
    c3.metric("Raw columns", f"{raw_df.shape[1]}")
    c4.metric("Processed columns", f"{processed_df.shape[1]}")


def render_raw_tab(raw_df: pd.DataFrame) -> None:
    st.subheader("Data before processing")
    st.caption("Source: `data/raw/top_1000_manga.json` (MangaDex top-rated titles + stats).")

    left, right = st.columns(2)
    with left:
        st.markdown("**Columns**")
        st.code("\n".join(raw_df.columns.tolist()))
    with right:
        st.markdown("**Missing values (top)**")
        missing = (
            raw_df.isna()
            .sum()
            .sort_values(ascending=False)
            .rename("missing_count")
            .reset_index()
            .rename(columns={"index": "column"})
        )
        st.dataframe(missing.head(12), use_container_width=True, hide_index=True)

    st.markdown("**Preview**")
    preview_cols = [
        c
        for c in [
            "rank",
            "title",
            "title_en",
            "status",
            "year",
            "follows",
            "rating_average",
            "rating_bayesian",
            "tags",
            "authors",
        ]
        if c in raw_df.columns
    ]
    st.dataframe(display_frame(raw_df[preview_cols]), use_container_width=True, hide_index=True)

    if "follows" in raw_df.columns:
        st.markdown("**Followers distribution (raw)**")
        chart = (
            alt.Chart(raw_df[["follows"]].dropna())
            .mark_bar(color="#c45c26")
            .encode(
                x=alt.X("follows:Q", bin=alt.Bin(maxbins=40), title="Followers"),
                y=alt.Y("count()", title="Manga count"),
                tooltip=["count()"],
            )
            .properties(height=280)
        )
        st.altair_chart(chart, use_container_width=True)


def render_processed_tab(raw_df: pd.DataFrame, processed_df: pd.DataFrame) -> None:
    st.subheader("Data after processing")
    st.caption(
        "Deduplicated titles, normalized tags, `follows_group` bins, "
        "and engineered flags (`is_award_winning`, `is_new`)."
    )

    added_cols = [c for c in processed_df.columns if c not in raw_df.columns]
    removed_rows = len(raw_df) - len(processed_df)

    m1, m2, m3 = st.columns(3)
    m1.metric("Rows removed (dedupe)", removed_rows)
    m2.metric("New columns", len(added_cols))
    m3.metric("Unique titles", processed_df["title"].nunique() if "title" in processed_df else 0)

    if added_cols:
        st.markdown("**Added during processing**")
        st.write(", ".join(f"`{c}`" for c in added_cols))

    if "follows_group" in processed_df.columns:
        group_counts = (
            processed_df["follows_group"]
            .astype(str)
            .value_counts()
            .rename_axis("follows_group")
            .reset_index(name="count")
        )
        order = ["Low", "Medium", "High", "Very High"]
        group_counts["follows_group"] = pd.Categorical(
            group_counts["follows_group"], categories=order, ordered=True
        )
        group_counts = group_counts.sort_values("follows_group")

        st.markdown("**Followers groups**")
        chart = (
            alt.Chart(group_counts)
            .mark_bar(color="#1f6f5b")
            .encode(
                x=alt.X("follows_group:N", sort=order, title="Followers group"),
                y=alt.Y("count:Q", title="Count"),
                tooltip=["follows_group", "count"],
            )
            .properties(height=280)
        )
        st.altair_chart(chart, use_container_width=True)

    preview_cols = [
        c
        for c in [
            "rank",
            "title",
            "title_en",
            "tags",
            "status",
            "year",
            "follows",
            "follows_group",
            "rating_average",
            "rating_bayesian",
            "is_award_winning",
            "is_new",
        ]
        if c in processed_df.columns
    ]
    st.markdown("**Preview**")
    st.dataframe(
        display_frame(processed_df[preview_cols]),
        use_container_width=True,
        hide_index=True,
    )


def render_model_tab(metrics: dict) -> None:
    st.subheader("Model evaluation (Stratified K-Fold)")
    st.caption(
        "RandomForest predicts `follows_group` from encoded manga features "
        "(ratings, rank, year, demographics, genre/theme tags, etc.)."
    )

    group_importance = pd.Series(metrics["group_importance"]).sort_values(ascending=False)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Accuracy", f"{metrics['accuracy']:.3f}")
    k2.metric("Precision", f"{metrics['precision']:.3f}")
    k3.metric("Recall", f"{metrics['recall']:.3f}")
    k4.metric("F1", f"{metrics['f1']:.3f}")
    k5.metric("ROC-AUC", f"{metrics['roc_auc']:.3f}")

    st.info(
        f"Most contributing field: **{metrics['most_contributing_field']}** "
        f"({group_importance.iloc[0]:.4f})"
    )

    left, right = st.columns(2)

    with left:
        st.markdown("**Confusion matrix** (rows = true, cols = predicted)")
        labels = metrics["labels"]
        cm_df = pd.DataFrame(metrics["confusion_matrix"], index=labels, columns=labels)
        st.dataframe(cm_df, use_container_width=True)

        cm_long = cm_df.reset_index().melt(
            id_vars="index", var_name="predicted", value_name="count"
        )
        cm_long = cm_long.rename(columns={"index": "actual"})
        heat = (
            alt.Chart(cm_long)
            .mark_rect()
            .encode(
                x=alt.X("predicted:N", sort=labels, title="Predicted"),
                y=alt.Y("actual:N", sort=labels, title="Actual"),
                color=alt.Color("count:Q", scale=alt.Scale(scheme="teals")),
                tooltip=["actual", "predicted", "count"],
            )
            .properties(height=320)
        )
        text = (
            alt.Chart(cm_long)
            .mark_text(color="#102a27")
            .encode(
                x=alt.X("predicted:N", sort=labels),
                y=alt.Y("actual:N", sort=labels),
                text="count:Q",
            )
        )
        st.altair_chart(heat + text, use_container_width=True)

    with right:
        st.markdown("**Field contribution**")
        imp = group_importance.rename("importance").reset_index()
        imp.columns = ["field", "importance"]
        bar = (
            alt.Chart(imp)
            .mark_bar(color="#2b4c7e")
            .encode(
                x=alt.X("importance:Q", title="Importance"),
                y=alt.Y("field:N", sort="-x", title=None),
                tooltip=["field", alt.Tooltip("importance:Q", format=".4f")],
            )
            .properties(height=360)
        )
        st.altair_chart(bar, use_container_width=True)

    st.markdown("**Classification report**")
    report = metrics.get("classification_report_dict", {})
    report_rows = []
    for label, values in report.items():
        if isinstance(values, dict):
            report_rows.append(
                {
                    "label": label,
                    "precision": values.get("precision"),
                    "recall": values.get("recall"),
                    "f1-score": values.get("f1-score"),
                    "support": values.get("support"),
                }
            )
    if report_rows:
        st.dataframe(pd.DataFrame(report_rows), use_container_width=True, hide_index=True)
    else:
        st.code(metrics["classification_report"])

    pred_df = pd.DataFrame(
        {"actual": metrics["y_true"], "predicted": metrics["y_pred"]}
    )
    st.markdown("**Prediction sample**")
    st.dataframe(pred_df.head(50), use_container_width=True, hide_index=True)


def render_genre_tab(genre_df: pd.DataFrame) -> None:
    st.subheader("Genre success likelihood")
    st.caption(
        "Aggregated from MangaDex **genre** tags only "
        "(excludes format/theme tags like Award Winning, Official Colored)."
    )

    if genre_df.empty:
        st.warning("No genre stats available.")
        return

    top = genre_df.iloc[0]
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Top genre", str(top["genre"]))
    g2.metric("Success score", f"{float(top['success_score']):.3f}")
    g3.metric("Avg followers", f"{float(top['avg_followers']):,.0f}")
    g4.metric("Avg rank", f"{float(top['avg_rank']):.1f}")

    chart_df = genre_df.head(15).copy()
    bar = (
        alt.Chart(chart_df)
        .mark_bar(color="#8b3a3a")
        .encode(
            x=alt.X("success_score:Q", title="Success score"),
            y=alt.Y("genre:N", sort="-x", title=None),
            tooltip=[
                "genre",
                alt.Tooltip("success_score:Q", format=".3f"),
                alt.Tooltip("avg_followers:Q", format=",.0f"),
                alt.Tooltip("avg_rating:Q", format=".3f"),
                alt.Tooltip("avg_rank:Q", format=".1f"),
                "manga_count",
            ],
        )
        .properties(height=420)
    )
    st.altair_chart(bar, use_container_width=True)

    st.markdown("**Full genre table**")
    st.dataframe(genre_df, use_container_width=True, hide_index=True)


def main() -> None:
    st.title("Manga Analysis Dashboard")
    st.write(
        "Explore the MangaDex top-1000 dataset before and after ETL processing, "
        "review K-Fold model results for follower-group prediction, and compare genre success."
    )

    with st.sidebar:
        st.header("Controls")
        if st.button("Clear cache & reload"):
            st.cache_data.clear()
            st.rerun()
        st.markdown("---")
        st.caption(f"Raw file: `{RAW_PATH}`")
        st.caption("Model: RandomForest + Stratified K-Fold")

    mtime = raw_mtime()
    try:
        raw_df = load_raw_data(mtime)
    except FileNotFoundError as exc:
        st.error(str(exc))
        st.stop()

    processed_df = load_processed_data(mtime)
    render_overview(raw_df, processed_df)

    tab_raw, tab_processed, tab_model, tab_genre = st.tabs(
        ["Before processing", "After processing", "Model evaluation", "Genre success"]
    )

    with tab_raw:
        render_raw_tab(raw_df)

    with tab_processed:
        render_processed_tab(raw_df, processed_df)

    with tab_model:
        metrics = load_model_results(mtime)
        render_model_tab(metrics)

    with tab_genre:
        genre_df = load_genre_stats(mtime)
        render_genre_tab(genre_df)


if __name__ == "__main__":
    main()
