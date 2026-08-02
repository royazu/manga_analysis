from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import MultiLabelBinarizer
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    roc_auc_score,
    precision_score,
    recall_score,
    f1_score,
    accuracy_score,
)


BASE_URL = "https://api.mangadex.org"
HEADERS = {"User-Agent": "manga-analysis/0.1 (local research project)"}
RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")

PAGE_SIZE = 100
TOP_N = 1000
REQUEST_PAUSE_S = 0.25
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


def _get(url: str, params: list[tuple[str, Any]] | dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(url, headers=HEADERS, params=params, timeout=60)
    response.raise_for_status()
    time.sleep(REQUEST_PAUSE_S)
    return response.json()


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


def save_outputs(rows: list[dict[str, Any]]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    json_path = RAW_DIR / "top_1000_manga.json"
    csv_path = PROCESSED_DIR / "top_1000_manga.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    flat_rows = []
    for row in rows:
        flat_rows.append(
            {
                "rank": row["rank"],
                "id": row["id"],
                "title": row["title"],
                "title_en": row["title_en"],
                "authors": "; ".join(row["authors"]),
                "artists": "; ".join(row["artists"]),
                "tags": "; ".join(row["tags"]),
                "publication_demographic": row["publication_demographic"],
                "status": row["status"],
                "year": row["year"],
                "content_rating": row["content_rating"],
                "original_language": row["original_language"],
                "follows": row["follows"],
                "rating_average": row["rating_average"],
                "rating_bayesian": row["rating_bayesian"],
                "comments_replies_count": row["comments_replies_count"],
                "related_count": len(row["related"]),
                "related": json.dumps(row["related"], ensure_ascii=False),
            }
        )
    _safe_to_csv(pd.DataFrame(flat_rows), csv_path)
    print(f"Saved {len(rows)} rows to {json_path} and {csv_path}")


def _stats_from_existing(path: Path) -> dict[str, dict[str, Any]] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        rows = json.load(f)
    stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        manga_id = row.get("id")
        if not manga_id:
            continue
        comments = None
        if row.get("comments_thread_id") is not None or row.get("comments_replies_count") is not None:
            comments = {
                "threadId": row.get("comments_thread_id"),
                "repliesCount": row.get("comments_replies_count"),
            }
        stats[manga_id] = {
            "follows": row.get("follows"),
            "rating": {
                "average": row.get("rating_average"),
                "bayesian": row.get("rating_bayesian"),
            },
            "comments": comments,
            "unavailableChaptersCount": row.get("unavailable_chapters_count"),
        }
    return stats


def check_api_status() -> str | None:
    # MangaDex uses top-level "result", not "status".
    response = requests.get(
        f"{BASE_URL}/manga",
        headers=HEADERS,
        params={"limit": 1},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("result")


def _to_tag_list(value: Any) -> list[str]:
    # JSON already stores tags as lists; CSV stores them as "Action; Romance".
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    if isinstance(value, str):
        return [tag.strip() for tag in value.split(";") if tag.strip()]
    return []


def data_preprocessing(data: pd.DataFrame) -> pd.DataFrame:
    data = data[['id', 'title', 'title_en', 'title_localized', 'tags',
       'publication_demographic', 'authors', 'artists', 'status', 'year',
       'content_rating', 'original_language', 'description_en', 'related',
       'created_at', 'updated_at', 'rank', 'follows', 'rating_average',
       'rating_bayesian']].copy()
    data.drop_duplicates('title', inplace=True)
    data['tags'] = data['tags'].apply(_to_tag_list)
    data['follows_group'] = pd.cut(
        data['follows'],
        bins=[0, 1000, 10000, 100000, 1000000],
        labels=['Low', 'Medium', 'High', 'Very High'],
    )
    return data


def etl_processing(data: pd.DataFrame) -> pd.DataFrame:
    frame = data.copy()
    frame["is_award_winning"] = frame["tags"].apply(
        lambda tags: any("award" in str(tag).lower() for tag in tags)
    )
    frame["is_new"] = frame["year"].fillna(0).astype(float) > 2025
    return frame


def build_feature_matrix(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, dict[str, list[str]]]:
    """Build numeric X from data. y = follows_group. Never include follows itself (leakage)."""
    frame = data.dropna(subset=["follows_group"]).copy()
    numeric_cols = ["year", "rank", "rating_average", "rating_bayesian"]
    boolean_cols = ["is_award_winning", "is_new"]
    categorical_cols = [
        "publication_demographic",
        "status",
        "content_rating",
        "original_language",
    ]

    frame["year"] = frame["year"].fillna(frame["year"].median())
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
    y = frame["follows_group"].astype(str)

    feature_groups: dict[str, list[str]] = {
        col: [col] for col in numeric_cols + boolean_cols
    }
    for col in categorical_cols:
        feature_groups[col] = [c for c in cats_encoded.columns if c.startswith(f"{col}_")]
    feature_groups["tags"] = list(tags_encoded.columns)
    return X, y, feature_groups


def model_training(data: pd.DataFrame, n_splits: int = 5):
    """
    Stratified K-Fold CV: X = encoded data features, y = follows_group.
    Confusion matrix evaluates class prediction quality.
    Feature importances identify the most contributing field.
    """
    X, y, feature_groups = build_feature_matrix(data)
    labels = sorted(y.unique(), key=lambda c: ["Low", "Medium", "High", "Very High"].index(c))
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
    group_importance = {
        group: float(importances[cols].sum()) if cols else 0.0
        for group, cols in feature_groups.items()
    }
    group_importance = pd.Series(group_importance).sort_values(ascending=False)

    cm = confusion_matrix(y, y_pred, labels=labels)
    metrics = {
        "accuracy": accuracy_score(y, y_pred),
        "precision": precision_score(y, y_pred, average="weighted", zero_division=0),
        "recall": recall_score(y, y_pred, average="weighted", zero_division=0),
        "f1": f1_score(y, y_pred, average="weighted", zero_division=0),
        "roc_auc": roc_auc_score(y, y_proba, multi_class="ovr", average="weighted"),
        "classification_report": classification_report(y, y_pred, labels=labels, zero_division=0),
        "classification_report_dict": classification_report(
            y, y_pred, labels=labels, zero_division=0, output_dict=True
        ),
        "confusion_matrix": cm,
        "labels": labels,
        "group_importance": group_importance,
        "most_contributing_field": group_importance.index[0],
    }
    return model, y, y_pred, metrics


FALLBACK_GENRE_TAGS = {
    "Action",
    "Adventure",
    "Boys' Love",
    "Comedy",
    "Crime",
    "Drama",
    "Fantasy",
    "Girls' Love",
    "Historical",
    "Horror",
    "Isekai",
    "Magical Girls",
    "Mecha",
    "Medical",
    "Mystery",
    "Philosophical",
    "Psychological",
    "Romance",
    "Sci-Fi",
    "Slice of Life",
    "Sports",
    "Superhero",
    "Thriller",
    "Tragedy",
    "Wuxia",
}


def fetch_genre_tag_names() -> set[str]:
    """MangaDex tag groups: genre / theme / format / content. Keep genre only."""
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


def validate_data_quality(raw_df: pd.DataFrame, processed_df: pd.DataFrame) -> pd.DataFrame:
    """Surface missing values, duplicates, and invalid ranges for reviewer visibility."""
    issues: list[dict[str, Any]] = []

    def add_issue(check: str, severity: str, detail: str, count: int = 1) -> None:
        issues.append(
            {"check": check, "severity": severity, "detail": detail, "count": count}
        )

    if raw_df.empty:
        add_issue("raw_empty", "error", "Raw dataset is empty")
    if processed_df.empty:
        add_issue("processed_empty", "error", "Processed dataset is empty")

    dup_titles = int(raw_df["title"].duplicated().sum()) if "title" in raw_df else 0
    if dup_titles:
        add_issue("duplicate_titles_raw", "warning", "Duplicate titles in raw extract", dup_titles)

    for col in ("follows", "rating_average", "rating_bayesian", "year", "rank"):
        if col not in processed_df.columns:
            continue
        missing = int(processed_df[col].isna().sum())
        if missing:
            add_issue(f"missing_{col}", "warning", f"Missing values in {col}", missing)

    if "follows" in processed_df.columns:
        invalid_follows = int((processed_df["follows"].fillna(-1) < 0).sum())
        if invalid_follows:
            add_issue("invalid_follows", "error", "Negative follower counts", invalid_follows)

    if "rating_average" in processed_df.columns:
        invalid_rating = int(
            (
                (processed_df["rating_average"] < 0) | (processed_df["rating_average"] > 10)
            ).fillna(False).sum()
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

    report = pd.DataFrame(issues)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _safe_to_csv(report, PROCESSED_DIR / "data_quality_report.csv")
    print(f"Data quality checks: {len(issues)} issue(s) written to {report_path}")
    if issues:
        print(report.to_string(index=False))
    else:
        print("Data quality checks: no issues found")
    return report


def _safe_to_csv(df: pd.DataFrame, path: Path) -> Path:
    """Write CSV; if the target is locked (e.g. open in Excel), write a sibling file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(path, index=False)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_new{path.suffix}")
        df.to_csv(alt, index=False)
        print(f"Could not overwrite {path} (file locked); saved {alt}")
        return alt


def genre_success_stats(data: pd.DataFrame, genre_names: set[str] | None = None) -> pd.DataFrame:
    """
    Aggregate manga stats by genre tag only (excludes format/theme/content tags
    such as Award Winning, Official Colored, etc.).
    """
    if genre_names is None:
        genre_names = fetch_genre_tag_names()

    frame = data.copy()
    frame["genres"] = frame["tags"].apply(
        lambda tags: [tag for tag in tags if tag in genre_names]
    )
    exploded = frame.explode("genres").dropna(subset=["genres"])
    exploded = exploded.rename(columns={"genres": "genre"})

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
        .sort_values(["avg_followers", "avg_bayesian_rating"], ascending=False)
        .reset_index(drop=True)
    )

    # Higher followers + rating and better (lower) rank => higher success likelihood.
    followers_norm = summary["avg_followers"] / summary["avg_followers"].max()
    rating_norm = summary["avg_bayesian_rating"] / summary["avg_bayesian_rating"].max()
    rank_norm = 1 - (summary["avg_rank"] - summary["avg_rank"].min()) / (
        summary["avg_rank"].max() - summary["avg_rank"].min()
    )
    summary["success_score"] = (
        0.45 * followers_norm + 0.35 * rating_norm + 0.20 * rank_norm
    )
    summary = summary.sort_values("success_score", ascending=False).reset_index(drop=True)
    return summary


def main() -> None:
    raw_path = RAW_DIR / "top_1000_manga.json"
    api_ok = False
    try:
        api_ok = check_api_status() == "ok"
    except Exception as exc:
        print(f"API health check failed: {exc}")

    if api_ok:
        print("API is running")
    else:
        print("API is unavailable; will use cached raw data if present")

    if raw_path.exists():
        with raw_path.open(encoding="utf-8") as f:
            json_data = json.load(f)
        raw_df = pd.DataFrame(json_data)
        print(f"Loaded cached raw data from {raw_path} ({len(raw_df)} rows)")
    else:
        if not api_ok:
            print("API is not running and no cached raw data found")
            return
        manga_rows = fetch_top_manga(TOP_N)
        stats_by_id = fetch_statistics([row["id"] for row in manga_rows])
        merged = merge_manga_with_stats(manga_rows, stats_by_id)
        save_outputs(merged)
        raw_df = pd.DataFrame(merged)

    data = data_preprocessing(raw_df)
    data = etl_processing(data)
    validate_data_quality(raw_df, data)
    _, _, _, metrics = model_training(data)

    print("K-Fold evaluation: predict follows_group from manga features")
    print("accuracy:", metrics["accuracy"])
    print(metrics["classification_report"])
    print("Confusion matrix (rows=true, cols=pred):", metrics["labels"])
    print(metrics["confusion_matrix"])
    print("roc_auc:", metrics["roc_auc"])
    print("precision:", metrics["precision"])
    print("recall:", metrics["recall"])
    print("f1:", metrics["f1"])
    print("\nField contribution (RandomForest importance, grouped):")
    print(metrics["group_importance"].to_string())
    print(
        "\nMost contributing field:",
        metrics["most_contributing_field"],
        f"({metrics['group_importance'].iloc[0]:.4f})",
    )
    print(
        "Note: the confusion matrix shows prediction quality by follows_group class; "
        "field contribution comes from model feature importance."
    )

    print("\nGenre success summary (genre tags only):")
    genre_df = genre_success_stats(data)
    genre_path = _safe_to_csv(genre_df, PROCESSED_DIR / "genre_success_stats.csv")
    print(genre_df.to_string(index=False))
    print(f"\nSaved genre stats to {genre_path}")
    top = genre_df.iloc[0]
    print(
        f"\nHighest success-likelihood genre: {top['genre']} "
        f"(score={top['success_score']:.4f}, "
        f"avg_followers={top['avg_followers']:.0f}, "
        f"avg_rating={top['avg_rating']:.3f}, "
        f"avg_rank={top['avg_rank']:.1f})"
    )


if __name__ == "__main__":
    main()
