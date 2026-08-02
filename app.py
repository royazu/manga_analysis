from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from serving_contract import (
    CLEANED_JSON_PATH,
    DQ_REPORT_PATH,
    FALLBACK_GENRE_TAGS,
    FEATURE_IMPORTANCE_PATH,
    GENRE_MIN_COUNT,
    GENRE_STATS_PATH,
    MODEL_METRICS_PATH,
    MODEL_PREDICTIONS_PATH,
    RAW_PATH,
    RUN_MANIFEST_PATH,
)

# Streamlit can keep a stale `etl` in sys.modules across edits; reload for current API.
import etl as _etl

_etl = importlib.reload(_etl)
genre_success_stats = _etl.genre_success_stats


def _compute_genre_stats(filtered: pd.DataFrame) -> pd.DataFrame:
    """Call genre_success_stats with kwargs supported by the loaded function."""
    kwargs = {}
    params = inspect.signature(genre_success_stats).parameters
    if "genre_names" in params:
        kwargs["genre_names"] = set(FALLBACK_GENRE_TAGS)
    if "min_count" in params:
        kwargs["min_count"] = GENRE_MIN_COUNT
    out = genre_success_stats(filtered, **kwargs)
    if "low_support" not in out.columns and "manga_count" in out.columns:
        out = out.copy()
        out["low_support"] = out["manga_count"] < GENRE_MIN_COUNT
    return out

st.set_page_config(
    page_title="Manga Analysis Dashboard",
    page_icon="📚",
    layout="wide",
)


def _stringify_cell(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def display_frame(df: pd.DataFrame, max_rows: int = 200) -> pd.DataFrame:
    shown = df.head(max_rows).copy()
    for col in shown.columns:
        if shown[col].dtype == object:
            shown[col] = shown[col].map(_stringify_cell)
    return shown


def _mtime(*paths: Path) -> float:
    times = [p.stat().st_mtime for p in paths if p.exists()]
    return max(times) if times else 0.0


def _resolve_genre_stats_path() -> Path:
    """Prefer the newest write when ETL falls back to *_new.csv (locked file)."""
    primary = GENRE_STATS_PATH
    fallback = GENRE_STATS_PATH.with_name("genre_success_stats_new.csv")
    candidates = [p for p in (primary, fallback) if p.exists()]
    if not candidates:
        return primary
    return max(candidates, key=lambda p: p.stat().st_mtime)


@st.cache_data(show_spinner="Loading serving artifacts...")
def load_serving_bundle(_mtime: float) -> dict:
    genre_path = _resolve_genre_stats_path()
    required = [CLEANED_JSON_PATH, MODEL_METRICS_PATH, genre_path, DQ_REPORT_PATH]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing processed artifacts. Run `python etl.py` first.\nMissing:\n- "
            + "\n- ".join(missing)
        )

    with CLEANED_JSON_PATH.open(encoding="utf-8") as f:
        cleaned = pd.DataFrame(json.load(f))
    with MODEL_METRICS_PATH.open(encoding="utf-8") as f:
        metrics = json.load(f)

    raw = None
    if RAW_PATH.exists():
        with RAW_PATH.open(encoding="utf-8") as f:
            raw = pd.DataFrame(json.load(f))

    genre = pd.read_csv(genre_path)
    dq = pd.read_csv(DQ_REPORT_PATH) if DQ_REPORT_PATH.stat().st_size else pd.DataFrame()
    preds = (
        pd.read_csv(MODEL_PREDICTIONS_PATH)
        if MODEL_PREDICTIONS_PATH.exists()
        else pd.DataFrame()
    )
    importance = (
        pd.read_csv(FEATURE_IMPORTANCE_PATH)
        if FEATURE_IMPORTANCE_PATH.exists()
        else pd.DataFrame(
            [{"field": k, "importance": v} for k, v in metrics.get("group_importance", {}).items()]
        )
    )
    manifest = {}
    if RUN_MANIFEST_PATH.exists():
        with RUN_MANIFEST_PATH.open(encoding="utf-8") as f:
            manifest = json.load(f)

    return {
        "raw": raw,
        "cleaned": cleaned,
        "genre": genre,
        "dq": dq,
        "metrics": metrics,
        "preds": preds,
        "importance": importance,
        "manifest": manifest,
    }


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.subheader("Filters (cleaned grain: one row = one manga)")
    statuses = sorted([s for s in df.get("status", pd.Series(dtype=str)).dropna().unique()])
    demos = sorted(
        [d for d in df.get("publication_demographic", pd.Series(dtype=str)).dropna().unique()]
    )
    langs = sorted(
        [x for x in df.get("original_language", pd.Series(dtype=str)).dropna().unique()]
    )

    selected_status = st.sidebar.multiselect("Status", statuses, default=statuses)
    selected_demo = st.sidebar.multiselect(
        "Demographic", demos, default=demos, help="Null demographics are excluded when filtered."
    )
    selected_lang = st.sidebar.multiselect("Original language", langs, default=langs)

    year_series = pd.to_numeric(df.get("year"), errors="coerce")
    year_min = int(year_series.min()) if year_series.notna().any() else 1970
    year_max = int(year_series.max()) if year_series.notna().any() else 2026
    year_range = st.sidebar.slider("Year range", year_min, year_max, (year_min, year_max))

    out = df.copy()
    if selected_status:
        out = out[out["status"].isin(selected_status)]
    if selected_demo:
        out = out[
            out["publication_demographic"].isna()
            | out["publication_demographic"].isin(selected_demo)
        ]
    if selected_lang:
        out = out[out["original_language"].isin(selected_lang)]
    years = pd.to_numeric(out["year"], errors="coerce")
    out = out[(years.isna()) | ((years >= year_range[0]) & (years <= year_range[1]))]
    st.sidebar.caption(f"Filtered manga rows: {len(out):,}")
    return out


def render_overview(bundle: dict, filtered: pd.DataFrame) -> None:
    manifest = bundle["manifest"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Raw rows", f"{len(bundle['raw']):,}" if bundle["raw"] is not None else "n/a")
    c2.metric("Cleaned rows", f"{len(bundle['cleaned']):,}")
    c3.metric("Filtered rows", f"{len(filtered):,}")
    c4.metric("DQ gate", str(manifest.get("dq_gate", "n/a")).upper())
    c5.metric("Model accuracy", f"{bundle['metrics'].get('accuracy', 0):.3f}")
    if manifest:
        st.caption(
            f"run_id={manifest.get('run_id', 'n/a')} · source={manifest.get('source', 'n/a')} · "
            f"duration={manifest.get('duration_seconds', 'n/a')}s · "
            f"leaky features excluded={manifest.get('excluded_leaky_features', [])}"
        )


def render_raw_tab(raw_df: pd.DataFrame | None) -> None:
    st.subheader("Data before processing")
    if raw_df is None:
        st.warning("Raw file not found.")
        return
    st.caption("Source: `data/raw/top_1000_manga.json` (immutable extract for reproducibility).")
    left, right = st.columns(2)
    with left:
        st.markdown("**Columns**")
        st.code("\n".join(raw_df.columns.tolist()))
    with right:
        missing = (
            raw_df.isna()
            .sum()
            .sort_values(ascending=False)
            .rename("missing_count")
            .reset_index()
            .rename(columns={"index": "column"})
        )
        st.markdown("**Missing values**")
        st.dataframe(missing.head(12), use_container_width=True, hide_index=True)

    cols = [c for c in ["rank", "title", "title_en", "status", "year", "follows", "rating_bayesian", "tags"] if c in raw_df.columns]
    st.dataframe(display_frame(raw_df[cols]), use_container_width=True, hide_index=True)


def render_processed_tab(cleaned: pd.DataFrame, filtered: pd.DataFrame) -> None:
    st.subheader("Data after processing")
    st.caption(
        "Deduplicated titles, normalized tags, `follows_group`, engineered flags. "
        "Sidebar filters apply at manga grain; genre tab recomputes from the filtered set."
    )
    added = sorted(set(cleaned.columns) - {"id", "title", "tags", "follows", "rank"})
    m1, m2, m3 = st.columns(3)
    m1.metric("Cleaned rows", len(cleaned))
    m2.metric("Filtered rows", len(filtered))
    m3.metric("Unique titles (filtered)", filtered["title"].nunique() if "title" in filtered else 0)

    if "follows_group" in filtered.columns:
        group_counts = (
            filtered["follows_group"]
            .astype(str)
            .value_counts()
            .rename_axis("follows_group")
            .reset_index(name="count")
        )
        order = ["Low", "Medium", "High", "Very High"]
        chart = (
            alt.Chart(group_counts)
            .mark_bar(color="#1f6f5b")
            .encode(
                x=alt.X("follows_group:N", sort=order),
                y="count:Q",
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
            "status",
            "year",
            "follows",
            "follows_group",
            "rating_average",
            "rating_bayesian",
            "is_award_winning",
            "is_new",
            "related_count",
        ]
        if c in filtered.columns
    ]
    st.dataframe(display_frame(filtered[preview_cols]), use_container_width=True, hide_index=True)


def render_dq_tab(dq: pd.DataFrame, manifest: dict) -> None:
    st.subheader("Data quality & trust")
    gate = str(manifest.get("dq_gate", "unknown")).upper()
    if gate == "PASS":
        st.success("DQ gate: PASS")
    elif gate == "WARN":
        st.warning("DQ gate: WARN — pipeline continued; review issues below.")
    elif gate == "FAIL":
        st.error("DQ gate: FAIL — serving artifacts should not be trusted until fixed.")
    else:
        st.info(f"DQ gate: {gate}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Errors", manifest.get("dq_errors", 0))
    c2.metric("Warnings", manifest.get("dq_warnings", 0))
    c3.metric("Issues", len(dq))

    st.markdown(
        "**Gate policy:** `error` fails ETL before serving; `warning` is recorded and shown here."
    )
    if dq.empty:
        st.write("No DQ issues recorded.")
    else:
        st.dataframe(dq, use_container_width=True, hide_index=True)

    st.markdown("**Reliability notes**")
    for note in manifest.get("notes", []):
        st.write(f"- {note}")


def render_model_tab(metrics: dict, preds: pd.DataFrame, importance: pd.DataFrame) -> None:
    st.subheader("Model evaluation (served artifact)")
    st.caption(
        "RandomForest + Stratified K-Fold predicting `follows_group`. "
        "`follows` and `rank` are excluded to reduce leakage. Metrics were computed in `etl.py`."
    )
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Accuracy", f"{metrics.get('accuracy', 0):.3f}")
    k2.metric("Precision", f"{metrics.get('precision', 0):.3f}")
    k3.metric("Recall", f"{metrics.get('recall', 0):.3f}")
    k4.metric("F1", f"{metrics.get('f1', 0):.3f}")
    k5.metric("ROC-AUC", f"{metrics.get('roc_auc', 0):.3f}")
    st.info(
        f"Most contributing field: **{metrics.get('most_contributing_field')}** · "
        f"Excluded leaky features: `{metrics.get('excluded_leaky_features')}`"
    )

    left, right = st.columns(2)
    labels = metrics.get("labels", [])
    with left:
        cm_df = pd.DataFrame(metrics.get("confusion_matrix", []), index=labels, columns=labels)
        st.markdown("**Confusion matrix**")
        st.dataframe(cm_df, use_container_width=True)
        if not cm_df.empty:
            cm_long = cm_df.reset_index().melt(id_vars="index", var_name="predicted", value_name="count")
            cm_long = cm_long.rename(columns={"index": "actual"})
            heat = (
                alt.Chart(cm_long)
                .mark_rect()
                .encode(
                    x=alt.X("predicted:N", sort=labels),
                    y=alt.Y("actual:N", sort=labels),
                    color=alt.Color("count:Q", scale=alt.Scale(scheme="teals")),
                    tooltip=["actual", "predicted", "count"],
                )
                .properties(height=320)
            )
            text = alt.Chart(cm_long).mark_text().encode(
                x=alt.X("predicted:N", sort=labels),
                y=alt.Y("actual:N", sort=labels),
                text="count:Q",
            )
            st.altair_chart(heat + text, use_container_width=True)
    with right:
        st.markdown("**Field contribution**")
        if not importance.empty:
            bar = (
                alt.Chart(importance)
                .mark_bar(color="#2b4c7e")
                .encode(
                    x="importance:Q",
                    y=alt.Y("field:N", sort="-x"),
                    tooltip=["field", alt.Tooltip("importance:Q", format=".4f")],
                )
                .properties(height=360)
            )
            st.altair_chart(bar, use_container_width=True)

    report = metrics.get("classification_report_dict", {})
    rows = []
    for label, values in report.items():
        if isinstance(values, dict):
            rows.append(
                {
                    "label": label,
                    "precision": values.get("precision"),
                    "recall": values.get("recall"),
                    "f1-score": values.get("f1-score"),
                    "support": values.get("support"),
                }
            )
    if rows:
        st.markdown("**Classification report**")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if not preds.empty:
        st.markdown("**Prediction sample**")
        st.dataframe(preds.head(50), use_container_width=True, hide_index=True)


def render_genre_tab(filtered: pd.DataFrame, baseline_genre: pd.DataFrame) -> None:
    st.subheader("Genre success likelihood")
    st.caption(
        f"Genre-only tags. Multi-label grain: one manga can appear in multiple genres. "
        f"Genres with n < {GENRE_MIN_COUNT} are flagged `low_support` and deprioritized."
    )

    recomputed = _compute_genre_stats(filtered)
    if recomputed.empty:
        st.warning("No genre stats for current filter.")
        return

    supported = recomputed.loc[~recomputed["low_support"]] if "low_support" in recomputed.columns else recomputed
    top = supported.iloc[0] if not supported.empty else recomputed.iloc[0]
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Top genre (filtered)", str(top["genre"]))
    g2.metric("Success score", f"{float(top['success_score']):.3f}" if pd.notna(top["success_score"]) else "n/a")
    g3.metric("Avg followers", f"{float(top['avg_followers']):,.0f}")
    g4.metric("Manga count", f"{int(top['manga_count'])}")

    chart_df = supported.head(15) if not supported.empty else recomputed.head(15)
    bar = (
        alt.Chart(chart_df)
        .mark_bar(color="#8b3a3a")
        .encode(
            x="success_score:Q",
            y=alt.Y("genre:N", sort="-x"),
            tooltip=[
                "genre",
                alt.Tooltip("success_score:Q", format=".3f"),
                alt.Tooltip("avg_followers:Q", format=",.0f"),
                "manga_count",
                "low_support",
            ],
        )
        .properties(height=420)
    )
    st.altair_chart(bar, use_container_width=True)

    st.markdown("**Filtered genre table**")
    st.dataframe(recomputed, use_container_width=True, hide_index=True)
    with st.expander("Baseline genre table from ETL (unfiltered)"):
        st.dataframe(baseline_genre, use_container_width=True, hide_index=True)


def main() -> None:
    st.title("Manga Analysis Dashboard")
    st.write(
        "Business-facing view over MangaDex top-rated titles: data trust, follower-group model, "
        "and genre success. The app **only reads processed artifacts** produced by `python etl.py`."
    )

    with st.sidebar:
        st.header("Controls")
        if st.button("Clear cache & reload"):
            st.cache_data.clear()
            st.rerun()
        st.caption("Serve path: processed only (no model training in UI).")

    mtime = _mtime(
        CLEANED_JSON_PATH,
        MODEL_METRICS_PATH,
        GENRE_STATS_PATH,
        DQ_REPORT_PATH,
        RUN_MANIFEST_PATH,
        RAW_PATH,
    )
    try:
        bundle = load_serving_bundle(mtime)
    except FileNotFoundError as exc:
        st.error(str(exc))
        st.stop()

    filtered = apply_filters(bundle["cleaned"])
    render_overview(bundle, filtered)

    tabs = st.tabs(
        [
            "Before processing",
            "After processing",
            "Data quality",
            "Model evaluation",
            "Genre success",
        ]
    )
    with tabs[0]:
        render_raw_tab(bundle["raw"])
    with tabs[1]:
        render_processed_tab(bundle["cleaned"], filtered)
    with tabs[2]:
        render_dq_tab(bundle["dq"], bundle["manifest"])
    with tabs[3]:
        render_model_tab(bundle["metrics"], bundle["preds"], bundle["importance"])
    with tabs[4]:
        render_genre_tab(filtered, bundle["genre"])


if __name__ == "__main__":
    main()
