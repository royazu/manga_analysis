from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import MultiLabelBinarizer

from serving_contract import (
    CLEANED_CSV_PATH,
    CLEANED_JSON_PATH,
    DQ_REPORT_PATH,
    FALLBACK_GENRE_TAGS,
    FEATURE_IMPORTANCE_PATH,
    GENRE_MIN_COUNT,
    GENRE_STATS_PATH,
    MODEL_METRICS_PATH,
    MODEL_PREDICTIONS_PATH,
    PROCESSED_DIR,
    RAW_DIR,
    RAW_PATH,
    RUN_MANIFEST_PATH,
)

BASE_URL = "https://api.mangadex.org"
HEADERS = {"User-Agent": "manga-analysis/0.1 (local research project)"}

PAGE_SIZE = 100
TOP_N = 1000
REQUEST_PAUSE_S = 0.25
# Excluded from follower-group model to avoid label leakage / popularity proxies.
LEAKY_FEATURE_COLS = {"follows", "follows_group", "rank", "is_popular"}

RELATED_KEEP = {
    "spin_off",
    "sequel",
    "prequel",
    "side_story",
    "main_story",
    "adapted_from",
    "based_on",
    "doujinshi",
    "same_franchise",
    "shared_universe",
    "alternate_story",
    "alternate_version",
}


def _safe_to_csv(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(path, index=False)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_new{path.suffix}")
        df.to_csv(alt, index=False)
        print(f"Could not overwrite {path} (file locked); saved {alt}")
        return alt


def _safe_to_json(payload: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_new{path.suffix}")
        with alt.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"Could not overwrite {path} (file locked); saved {alt}")
        return alt


def _get(
    url: str,
    params: list[tuple[str, Any]] | dict[str, Any] | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    """GET with basic retry/backoff for 429 and transient network errors."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=60)
            if response.status_code == 429:
                sleep_s = REQUEST_PAUSE_S * (2**attempt) * 4
                print(f"Rate limited (429). Sleeping {sleep_s:.1f}s then retrying...")
                time.sleep(sleep_s)
                continue
            response.raise_for_status()
            request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
            payload = response.json()
            if request_id:
                payload["_request_id"] = request_id
            time.sleep(REQUEST_PAUSE_S)
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            sleep_s = REQUEST_PAUSE_S * (2**attempt) * 2
            print(f"Request failed ({exc}); retry in {sleep_s:.1f}s")
            time.sleep(sleep_s)
    raise RuntimeError(f"API request failed after {retries} attempts: {url}") from last_error


def _localized_title(title_map: dict[str, str] | None) -> str | None:
    if not title_map:
        return None
    for key in ("en", "ja-ro", "ja", "ko-ro", "zh-ro"):
        if key in title_map:
            return title_map[key]
    return next(iter(title_map.values()), None)


def _english_title(
    title_map: dict[str, str] | None,
    alt_titles: list[dict[str, str]] | None = None,
) -> str | None:
    if title_map and title_map.get("en"):
        return title_map["en"]
    for alt in alt_titles or []:
        if alt.get("en"):
            return alt["en"]
    return None


def fetch_top_manga(n: int = TOP_N, page_size: int = PAGE_SIZE) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, n, page_size):
        payload = _get(
            f"{BASE_URL}/manga",
            params=[
                ("limit", page_size),
                ("offset", offset),
                ("order[rating]", "desc"),
                ("includes[]", "author"),
                ("includes[]", "artist"),
                ("includes[]", "manga"),
            ],
        )
        for manga in payload.get("data", []):
            attrs = manga["attributes"]
            authors = [
                rel["attributes"]["name"]
                for rel in manga["relationships"]
                if rel["type"] == "author" and rel.get("attributes")
            ]
            artists = [
                rel["attributes"]["name"]
                for rel in manga["relationships"]
                if rel["type"] == "artist" and rel.get("attributes")
            ]
            tags = [
                tag["attributes"]["name"].get("en")
                for tag in attrs.get("tags", [])
                if tag.get("attributes", {}).get("name", {}).get("en")
            ]
            related = [
                {
                    "id": rel["id"],
                    "relation": rel.get("related"),
                    "title": _localized_title((rel.get("attributes") or {}).get("title")),
                }
                for rel in manga["relationships"]
                if rel["type"] == "manga" and rel.get("related") in RELATED_KEEP
            ]
            rows.append(
                {
                    "id": manga["id"],
                    "title": _localized_title(attrs.get("title")),
                    "title_en": _english_title(attrs.get("title"), attrs.get("altTitles")),
                    "title_localized": attrs.get("title"),
                    "tags": tags,
                    "publication_demographic": attrs.get("publicationDemographic"),
                    "authors": authors,
                    "artists": artists,
                    "status": attrs.get("status"),
                    "year": attrs.get("year"),
                    "content_rating": attrs.get("contentRating"),
                    "original_language": attrs.get("originalLanguage"),
                    "description_en": (attrs.get("description") or {}).get("en"),
                    "related": related,
                    "created_at": attrs.get("createdAt"),
                    "updated_at": attrs.get("updatedAt"),
                }
            )
        print(f"Fetched manga page offset={offset} ({len(rows)}/{n})")
    return rows[:n]


def fetch_statistics(manga_ids: list[str], batch_size: int = PAGE_SIZE) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for start in range(0, len(manga_ids), batch_size):
        batch = manga_ids[start : start + batch_size]
        payload = _get(
            f"{BASE_URL}/statistics/manga",
            params=[("manga[]", manga_id) for manga_id in batch],
        )
        stats.update(payload.get("statistics", {}))
        print(f"Fetched statistics {min(start + batch_size, len(manga_ids))}/{len(manga_ids)}")
    return stats


def merge_manga_with_stats(
    manga_rows: list[dict[str, Any]],
    stats_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for rank, row in enumerate(manga_rows, start=1):
        stats = stats_by_id.get(row["id"], {})
        rating = stats.get("rating") or {}
        comments = stats.get("comments") or {}
        merged.append(
            {
                **row,
                "rank": rank,
                "follows": stats.get("follows"),
                "rating_average": rating.get("average"),
                "rating_bayesian": rating.get("bayesian"),
                "comments_thread_id": comments.get("threadId"),
                "comments_replies_count": comments.get("repliesCount"),
                "unavailable_chapters_count": stats.get("unavailableChaptersCount"),
            }
        )
    return merged


def save_raw(rows: list[dict[str, Any]]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    _safe_to_json(rows, RAW_PATH)
    print(f"Saved raw extract to {RAW_PATH} ({len(rows)} rows)")


def check_api_status() -> str | None:
    response = requests.get(
        f"{BASE_URL}/manga",
        headers=HEADERS,
        params={"limit": 1},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("result")


def _to_tag_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    if isinstance(value, str):
        return [tag.strip() for tag in value.split(";") if tag.strip()]
    return []


def data_preprocessing(data: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "id",
        "title",
        "title_en",
        "title_localized",
        "tags",
        "publication_demographic",
        "authors",
        "artists",
        "status",
        "year",
        "content_rating",
        "original_language",
        "description_en",
        "related",
        "created_at",
        "updated_at",
        "rank",
        "follows",
        "rating_average",
        "rating_bayesian",
    ]
    frame = data[[c for c in cols if c in data.columns]].copy()
    frame.drop_duplicates("title", inplace=True)
    frame["tags"] = frame["tags"].apply(_to_tag_list)
    if "authors" in frame.columns:
        frame["authors"] = frame["authors"].apply(
            lambda v: v if isinstance(v, list) else _to_tag_list(v)
        )
    frame["follows_group"] = pd.cut(
        frame["follows"],
        bins=[0, 1000, 10000, 100000, 1000000],
        labels=["Low", "Medium", "High", "Very High"],
    )
    return frame


def etl_processing(data: pd.DataFrame) -> pd.DataFrame:
    frame = data.copy()
    frame["is_award_winning"] = frame["tags"].apply(
        lambda tags: any("award" in str(tag).lower() for tag in tags)
    )
    frame["is_new"] = frame["year"].fillna(0).astype(float) >= 2024
    frame["related_count"] = frame["related"].apply(
        lambda rel: len(rel) if isinstance(rel, list) else 0
    )
    return frame


def build_feature_matrix(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, dict[str, list[str]]]:
    """
    Build numeric X for follows_group prediction.
    Explicitly excludes follows/rank/follows_group to reduce leakage.
    """
    frame = data.dropna(subset=["follows_group"]).copy()
    numeric_cols = ["year", "rating_average", "rating_bayesian", "related_count"]
    boolean_cols = ["is_award_winning", "is_new"]
    categorical_cols = [
        "publication_demographic",
        "status",
        "content_rating",
        "original_language",
    ]

    frame["year"] = frame["year"].fillna(frame["year"].median())
    frame["related_count"] = frame.get("related_count", 0).fillna(0)
    for col in categorical_cols:
        frame[col] = frame[col].fillna("unknown").astype(str)
    for col in boolean_cols:
        frame[col] = frame[col].fillna(False).astype(int)

    mlb = MultiLabelBinarizer()
    tags_encoded = pd.DataFrame(
        mlb.fit_transform(frame["tags"]),
        columns=[f"tag_{name}" for name in mlb.classes_],
        index=frame.index,
    )
    cats_encoded = pd.get_dummies(frame[categorical_cols], drop_first=False)

    X = pd.concat(
        [frame[numeric_cols + boolean_cols], cats_encoded, tags_encoded],
        axis=1,
    )
    # Guardrail against accidental leakage columns.
    leak_cols = [c for c in X.columns if c in LEAKY_FEATURE_COLS or c.startswith("follows")]
    if leak_cols:
        X = X.drop(columns=leak_cols)

    y = frame["follows_group"].astype(str)
    feature_groups: dict[str, list[str]] = {
        col: [col] for col in numeric_cols + boolean_cols if col in X.columns
    }
    for col in categorical_cols:
        feature_groups[col] = [c for c in cats_encoded.columns if c.startswith(f"{col}_")]
    feature_groups["tags"] = list(tags_encoded.columns)
    return X, y, feature_groups


def model_training(data: pd.DataFrame, n_splits: int = 5):
    X, y, feature_groups = build_feature_matrix(data)
    labels = sorted(
        y.unique(),
        key=lambda c: ["Low", "Medium", "High", "Very High"].index(c)
        if c in {"Low", "Medium", "High", "Very High"}
        else 99,
    )
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    model = RandomForestClassifier(
        n_estimators=200,
        random_state=42,
        class_weight="balanced_subsample",
    )

    y_pred = cross_val_predict(model, X, y, cv=cv)
    y_proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba")
    model.fit(X, y)

    importances = pd.Series(model.feature_importances_, index=X.columns)
    group_importance = pd.Series(
        {
            group: float(importances[cols].sum()) if cols else 0.0
            for group, cols in feature_groups.items()
        }
    ).sort_values(ascending=False)

    metrics = {
        "accuracy": float(accuracy_score(y, y_pred)),
        "precision": float(precision_score(y, y_pred, average="weighted", zero_division=0)),
        "recall": float(recall_score(y, y_pred, average="weighted", zero_division=0)),
        "f1": float(f1_score(y, y_pred, average="weighted", zero_division=0)),
        "roc_auc": float(roc_auc_score(y, y_proba, multi_class="ovr", average="weighted")),
        "classification_report": classification_report(y, y_pred, labels=labels, zero_division=0),
        "classification_report_dict": classification_report(
            y, y_pred, labels=labels, zero_division=0, output_dict=True
        ),
        "confusion_matrix": confusion_matrix(y, y_pred, labels=labels).tolist(),
        "labels": labels,
        "group_importance": group_importance.to_dict(),
        "most_contributing_field": str(group_importance.index[0]),
        "excluded_leaky_features": sorted(LEAKY_FEATURE_COLS),
        "n_features": int(X.shape[1]),
        "n_rows": int(X.shape[0]),
    }
    return model, y, y_pred, metrics


def fetch_genre_tag_names() -> set[str]:
    try:
        payload = _get(f"{BASE_URL}/manga/tag")
        genres = {
            tag["attributes"]["name"].get("en")
            for tag in payload.get("data", [])
            if tag.get("attributes", {}).get("group") == "genre"
            and tag.get("attributes", {}).get("name", {}).get("en")
        }
        if genres:
            return genres
    except Exception as exc:
        print(f"Genre tag fetch failed ({exc}); using fallback genre list.")
    return set(FALLBACK_GENRE_TAGS)


def validate_data_quality(raw_df: pd.DataFrame, processed_df: pd.DataFrame) -> dict[str, Any]:
    """DQ checks with gate status: pass | warn | fail."""
    issues: list[dict[str, Any]] = []

    def add_issue(check: str, severity: str, detail: str, count: int = 1) -> None:
        issues.append(
            {"check": check, "severity": severity, "detail": detail, "count": int(count)}
        )

    if raw_df.empty:
        add_issue("raw_empty", "error", "Raw dataset is empty")
    if processed_df.empty:
        add_issue("processed_empty", "error", "Processed dataset is empty")

    if "title" in raw_df.columns:
        dup_titles = int(raw_df["title"].duplicated().sum())
        if dup_titles:
            add_issue("duplicate_titles_raw", "warning", "Duplicate titles in raw extract", dup_titles)

    for col in ("follows", "rating_average", "rating_bayesian", "year", "rank"):
        if col in processed_df.columns:
            missing = int(processed_df[col].isna().sum())
            if missing:
                add_issue(f"missing_{col}", "warning", f"Missing values in {col}", missing)

    if "follows" in processed_df.columns:
        invalid_follows = int((processed_df["follows"].fillna(-1) < 0).sum())
        if invalid_follows:
            add_issue("invalid_follows", "error", "Negative follower counts", invalid_follows)
        missing_stats = int(processed_df["follows"].isna().sum())
        if missing_stats:
            add_issue("missing_stats_coverage", "error", "Manga missing follower stats", missing_stats)

    if "rating_average" in processed_df.columns:
        invalid_rating = int(
            ((processed_df["rating_average"] < 0) | (processed_df["rating_average"] > 10))
            .fillna(False)
            .sum()
        )
        if invalid_rating:
            add_issue(
                "invalid_rating_average",
                "error",
                "rating_average outside expected 0-10 range",
                invalid_rating,
            )

    if "status" in processed_df.columns:
        allowed_status = {"ongoing", "completed", "hiatus", "cancelled"}
        unexpected = sorted(set(processed_df["status"].dropna().astype(str)) - allowed_status)
        if unexpected:
            add_issue(
                "unexpected_status",
                "warning",
                f"Unexpected status values: {unexpected}",
                len(unexpected),
            )

    if "follows_group" in processed_df.columns:
        ungrouped = int(processed_df["follows_group"].isna().sum())
        if ungrouped:
            add_issue(
                "ungrouped_follows",
                "warning",
                "Rows without follows_group (outside bin edges or null follows)",
                ungrouped,
            )

    if "related" in processed_df.columns and "id" in processed_df.columns:
        known_ids = set(processed_df["id"].dropna().astype(str))
        orphan = 0
        for rels in processed_df["related"]:
            if not isinstance(rels, list):
                continue
            for rel in rels:
                rid = str((rel or {}).get("id", ""))
                if rid and rid not in known_ids:
                    orphan += 1
        if orphan:
            add_issue(
                "related_ids_outside_sample",
                "warning",
                "Related manga IDs not present in top-1000 sample (expected for graph edges)",
                orphan,
            )

    if "authors" in processed_df.columns:
        missing_authors = int(processed_df["authors"].apply(lambda a: not a).sum())
        if missing_authors:
            add_issue(
                "missing_authors",
                "warning",
                "Rows with empty author expansion",
                missing_authors,
            )

    report = pd.DataFrame(issues, columns=["check", "severity", "detail", "count"])
    _safe_to_csv(report, DQ_REPORT_PATH)

    error_count = int((report["severity"] == "error").sum()) if not report.empty else 0
    warn_count = int((report["severity"] == "warning").sum()) if not report.empty else 0
    if error_count:
        gate = "fail"
    elif warn_count:
        gate = "warn"
    else:
        gate = "pass"

    summary = {
        "gate": gate,
        "error_count": error_count,
        "warning_count": warn_count,
        "issue_count": len(issues),
        "report_path": str(DQ_REPORT_PATH),
    }
    print(
        f"Data quality gate={gate} "
        f"(errors={error_count}, warnings={warn_count}) -> {DQ_REPORT_PATH}"
    )
    if issues:
        print(report.to_string(index=False))
    return summary


def genre_success_stats(
    data: pd.DataFrame,
    genre_names: set[str] | None = None,
    min_count: int = GENRE_MIN_COUNT,
) -> pd.DataFrame:
    """
    Aggregate by MangaDex genre tags only.
    Grain note: one manga can appear in multiple genres (multi-label).
    Genres below min_count are kept but flagged as low_support.
    """
    if genre_names is None:
        genre_names = fetch_genre_tag_names()

    frame = data.copy()
    frame["genres"] = frame["tags"].apply(lambda tags: [tag for tag in tags if tag in genre_names])
    exploded = frame.explode("genres").dropna(subset=["genres"]).rename(columns={"genres": "genre"})
    if exploded.empty:
        return pd.DataFrame(
            columns=[
                "genre",
                "manga_count",
                "avg_rating",
                "avg_bayesian_rating",
                "total_followers",
                "avg_followers",
                "avg_rank",
                "success_score",
                "low_support",
            ]
        )

    summary = (
        exploded.groupby("genre", as_index=False)
        .agg(
            manga_count=("id", "nunique"),
            avg_rating=("rating_average", "mean"),
            avg_bayesian_rating=("rating_bayesian", "mean"),
            total_followers=("follows", "sum"),
            avg_followers=("follows", "mean"),
            avg_rank=("rank", "mean"),
        )
        .reset_index(drop=True)
    )
    summary["low_support"] = summary["manga_count"] < min_count

    eligible = summary.loc[~summary["low_support"]].copy()
    if eligible.empty:
        eligible = summary.copy()

    followers_norm = eligible["avg_followers"] / eligible["avg_followers"].max()
    rating_norm = eligible["avg_bayesian_rating"] / eligible["avg_bayesian_rating"].max()
    rank_norm = 1 - (eligible["avg_rank"] - eligible["avg_rank"].min()) / max(
        eligible["avg_rank"].max() - eligible["avg_rank"].min(), 1e-9
    )
    eligible["success_score"] = 0.45 * followers_norm + 0.35 * rating_norm + 0.20 * rank_norm

    summary = summary.merge(
        eligible[["genre", "success_score"]],
        on="genre",
        how="left",
    )
    summary = summary.sort_values(
        ["low_support", "success_score", "manga_count"],
        ascending=[True, False, False],
    ).reset_index(drop=True)
    return summary


def _records_for_json(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = df.copy()
    if "follows_group" in records.columns:
        records["follows_group"] = records["follows_group"].astype("object")
        records["follows_group"] = records["follows_group"].where(
            records["follows_group"].notna(), None
        )
        records["follows_group"] = records["follows_group"].apply(
            lambda v: None if v is None else str(v)
        )
    return json.loads(records.to_json(orient="records"))


def persist_serving_artifacts(
    cleaned: pd.DataFrame,
    genre_df: pd.DataFrame,
    y_true: pd.Series,
    y_pred,
    metrics: dict[str, Any],
    dq_summary: dict[str, Any],
    run_id: str,
    started_at: datetime,
    source: str,
) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    _safe_to_json(_records_for_json(cleaned), CLEANED_JSON_PATH)

    flat = cleaned.copy()
    for col in ("tags", "authors", "artists"):
        if col in flat.columns:
            flat[col] = flat[col].apply(
                lambda v: "; ".join(map(str, v)) if isinstance(v, list) else v
            )
    if "related" in flat.columns:
        flat["related"] = flat["related"].apply(
            lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
        )
    if "title_localized" in flat.columns:
        flat["title_localized"] = flat["title_localized"].apply(
            lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v
        )
    if "follows_group" in flat.columns:
        flat["follows_group"] = flat["follows_group"].astype(str)
    _safe_to_csv(flat, CLEANED_CSV_PATH)
    _safe_to_csv(genre_df, GENRE_STATS_PATH)

    pred_df = pd.DataFrame(
        {
            "id": cleaned.loc[y_true.index, "id"].values if "id" in cleaned.columns else range(len(y_true)),
            "title": cleaned.loc[y_true.index, "title"].values if "title" in cleaned.columns else None,
            "actual": y_true.astype(str).values,
            "predicted": list(y_pred),
        }
    )
    _safe_to_csv(pred_df, MODEL_PREDICTIONS_PATH)

    importance_df = (
        pd.Series(metrics["group_importance"], name="importance")
        .rename_axis("field")
        .reset_index()
        .sort_values("importance", ascending=False)
    )
    _safe_to_csv(importance_df, FEATURE_IMPORTANCE_PATH)
    _safe_to_json(metrics, MODEL_METRICS_PATH)

    ended_at = datetime.now(timezone.utc)
    manifest = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round((ended_at - started_at).total_seconds(), 2),
        "source": source,
        "raw_rows": None,
        "cleaned_rows": int(len(cleaned)),
        "genre_rows": int(len(genre_df)),
        "dq_gate": dq_summary.get("gate"),
        "dq_errors": dq_summary.get("error_count"),
        "dq_warnings": dq_summary.get("warning_count"),
        "model_accuracy": metrics.get("accuracy"),
        "most_contributing_field": metrics.get("most_contributing_field"),
        "excluded_leaky_features": metrics.get("excluded_leaky_features"),
        "genre_min_count": GENRE_MIN_COUNT,
        "artifacts": {
            "raw": str(RAW_PATH),
            "cleaned_json": str(CLEANED_JSON_PATH),
            "cleaned_csv": str(CLEANED_CSV_PATH),
            "genre_stats": str(GENRE_STATS_PATH),
            "dq_report": str(DQ_REPORT_PATH),
            "model_metrics": str(MODEL_METRICS_PATH),
            "model_predictions": str(MODEL_PREDICTIONS_PATH),
            "feature_importance": str(FEATURE_IMPORTANCE_PATH),
        },
        "notes": [
            "App serves only processed artifacts; it does not retrain models.",
            "Genre stats are multi-label (manga can count in multiple genres).",
            "Genres with manga_count < genre_min_count are flagged low_support.",
        ],
    }
    _safe_to_json(manifest, RUN_MANIFEST_PATH)
    print(f"Wrote serving artifacts + run manifest ({RUN_MANIFEST_PATH})")


def main() -> None:
    started_at = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    print(f"ETL run_id={run_id}")

    api_ok = False
    try:
        api_ok = check_api_status() == "ok"
    except Exception as exc:
        print(f"API health check failed: {exc}")

    if api_ok:
        print("API is running")
    else:
        print("API is unavailable; will use cached raw data if present")

    source = "cache"
    if RAW_PATH.exists():
        with RAW_PATH.open(encoding="utf-8") as f:
            raw_df = pd.DataFrame(json.load(f))
        print(f"Loaded cached raw data from {RAW_PATH} ({len(raw_df)} rows)")
    else:
        if not api_ok:
            raise SystemExit("API is not running and no cached raw data found")
        source = "api"
        manga_rows = fetch_top_manga(TOP_N)
        stats_by_id = fetch_statistics([row["id"] for row in manga_rows])
        merged = merge_manga_with_stats(manga_rows, stats_by_id)
        save_raw(merged)
        raw_df = pd.DataFrame(merged)

    cleaned = etl_processing(data_preprocessing(raw_df))
    dq_summary = validate_data_quality(raw_df, cleaned)
    if dq_summary["gate"] == "fail":
        raise SystemExit(
            "ETL aborted: data quality gate=fail. "
            f"See {DQ_REPORT_PATH}. Fix source issues or relax rules before serving."
        )

    _, y_true, y_pred, metrics = model_training(cleaned)
    print("K-Fold evaluation complete")
    print(f"accuracy={metrics['accuracy']:.3f} f1={metrics['f1']:.3f}")
    print(f"most_contributing_field={metrics['most_contributing_field']}")
    print(f"excluded_leaky_features={metrics['excluded_leaky_features']}")

    genre_df = genre_success_stats(cleaned, min_count=GENRE_MIN_COUNT)
    print("\nGenre success summary (genre tags only, min_count applied to ranking):")
    print(genre_df.head(15).to_string(index=False))

    persist_serving_artifacts(
        cleaned=cleaned,
        genre_df=genre_df,
        y_true=y_true,
        y_pred=y_pred,
        metrics=metrics,
        dq_summary=dq_summary,
        run_id=run_id,
        started_at=started_at,
        source=source,
    )

    # Attach raw row count to manifest after write.
    if RUN_MANIFEST_PATH.exists():
        with RUN_MANIFEST_PATH.open(encoding="utf-8") as f:
            manifest = json.load(f)
        manifest["raw_rows"] = int(len(raw_df))
        _safe_to_json(manifest, RUN_MANIFEST_PATH)

    top = genre_df.loc[~genre_df["low_support"]].head(1)
    if top.empty:
        top = genre_df.head(1)
    if not top.empty:
        row = top.iloc[0]
        print(
            f"\nHighest success-likelihood genre (support-aware): {row['genre']} "
            f"(score={row['success_score']:.4f}, n={int(row['manga_count'])})"
        )
    print("ETL finished. Streamlit should read processed artifacts only.")


if __name__ == "__main__":
    main()
