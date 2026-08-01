from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sklearn.model_selection import train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import MultiLabelBinarizer
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    roc_auc_score,
    precision_score,
    recall_score,
    f1_score,
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
    pd.DataFrame(flat_rows).to_csv(csv_path, index=False)
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

def model_training(data: pd.DataFrame):
    # KNN needs numeric features only — text columns like title cannot be used raw.
    frame = data.dropna(subset=["follows_group"]).copy()
    numeric_cols = ["year", "rank", "rating_average", "rating_bayesian"]
    categorical_cols = [
        "publication_demographic",
        "status",
        "content_rating",
        "original_language",
    ]

    frame["year"] = frame["year"].fillna(frame["year"].median())
    for col in categorical_cols:
        frame[col] = frame[col].fillna("unknown").astype(str)

    mlb = MultiLabelBinarizer()
    tags_encoded = pd.DataFrame(
        mlb.fit_transform(frame["tags"]),
        columns=[f"tag_{name}" for name in mlb.classes_],
        index=frame.index,
    )
    cats_encoded = pd.get_dummies(frame[categorical_cols], drop_first=True)
    X = pd.concat([frame[numeric_cols], cats_encoded, tags_encoded], axis=1)
    y = frame["follows_group"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    model = KNeighborsClassifier(n_neighbors=4)
    model.fit(X_train, y_train)
    return model, X_test, y_test


def main() -> None:
    if check_api_status() != "ok":
        print("API is not running")
        return
    print("API is running")

    raw_path = RAW_DIR / "top_1000_manga.json"
    if raw_path.exists():
        with raw_path.open(encoding="utf-8") as f:
            json_data = json.load(f)
        data = pd.DataFrame(json_data)
    else:
        manga_rows = fetch_top_manga(TOP_N)
        stats_by_id = _stats_from_existing(raw_path)
        if stats_by_id is None or any(row["id"] not in stats_by_id for row in manga_rows):
            stats_by_id = fetch_statistics([row["id"] for row in manga_rows])
        else:
            print(f"Reused statistics from {raw_path}")
        merged = merge_manga_with_stats(manga_rows, stats_by_id)
        save_outputs(merged)
        data = pd.DataFrame(merged)

    data = data_preprocessing(data)
    model, X_test, y_test = model_training(data)
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)

    print(y_pred)
    print(y_test)
    print("accuracy:", model.score(X_test, y_test))
    print(classification_report(y_test, y_pred))
    print(confusion_matrix(y_test, y_pred))
    print(
        "roc_auc:",
        roc_auc_score(y_test, y_proba, multi_class="ovr", average="weighted"),
    )
    print("precision:", precision_score(y_test, y_pred, average="weighted", zero_division=0))
    print("recall:", recall_score(y_test, y_pred, average="weighted", zero_division=0))
    print("f1:", f1_score(y_test, y_pred, average="weighted", zero_division=0))


if __name__ == "__main__":
    main()
