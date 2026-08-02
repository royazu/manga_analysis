import pandas as pd

from etl import (
    LEAKY_FEATURE_COLS,
    _to_tag_list,
    build_feature_matrix,
    data_preprocessing,
    etl_processing,
    genre_success_stats,
    validate_data_quality,
)


def test_to_tag_list_handles_list_and_string():
    assert _to_tag_list(["Action", " Romance "]) == ["Action", "Romance"]
    assert _to_tag_list("Action; Romance") == ["Action", "Romance"]
    assert _to_tag_list(None) == []


def test_feature_matrix_excludes_leaky_columns():
    df = pd.DataFrame(
        [
            {
                "id": "a",
                "title": "A",
                "title_en": "A",
                "title_localized": {"en": "A"},
                "tags": ["Action", "Drama"],
                "publication_demographic": "shounen",
                "authors": ["X"],
                "artists": ["X"],
                "status": "ongoing",
                "year": 2010,
                "content_rating": "safe",
                "original_language": "ja",
                "description_en": "d",
                "related": [],
                "created_at": None,
                "updated_at": None,
                "rank": 1,
                "follows": 50000,
                "rating_average": 9.1,
                "rating_bayesian": 9.0,
            },
            {
                "id": "b",
                "title": "B",
                "title_en": "B",
                "title_localized": {"en": "B"},
                "tags": ["Romance"],
                "publication_demographic": "josei",
                "authors": ["Y"],
                "artists": ["Y"],
                "status": "completed",
                "year": 2015,
                "content_rating": "suggestive",
                "original_language": "ja",
                "description_en": "d",
                "related": [{"id": "z", "relation": "sequel"}],
                "created_at": None,
                "updated_at": None,
                "rank": 2,
                "follows": 8000,
                "rating_average": 8.8,
                "rating_bayesian": 8.7,
            },
            {
                "id": "c",
                "title": "C",
                "title_en": "C",
                "title_localized": {"en": "C"},
                "tags": ["Comedy"],
                "publication_demographic": "seinen",
                "authors": ["Z"],
                "artists": ["Z"],
                "status": "ongoing",
                "year": 2020,
                "content_rating": "safe",
                "original_language": "ko",
                "description_en": "d",
                "related": [],
                "created_at": None,
                "updated_at": None,
                "rank": 3,
                "follows": 120000,
                "rating_average": 9.2,
                "rating_bayesian": 9.1,
            },
        ]
    )
    cleaned = etl_processing(data_preprocessing(df))
    X, y, _ = build_feature_matrix(cleaned)
    assert "rank" not in X.columns
    assert "follows" not in X.columns
    assert not any(col in LEAKY_FEATURE_COLS for col in X.columns)
    assert len(y) == len(X) == 3


def test_genre_min_support_flag():
    df = pd.DataFrame(
        {
            "id": [f"id{i}" for i in range(25)],
            "tags": [["Action"]] * 22 + [["Mecha"]] * 3,
            "rating_average": [9.0] * 25,
            "rating_bayesian": [9.0] * 25,
            "follows": [10000] * 25,
            "rank": list(range(1, 26)),
        }
    )
    stats = genre_success_stats(df, genre_names={"Action", "Mecha"}, min_count=20)
    action = stats.loc[stats["genre"] == "Action"].iloc[0]
    mecha = stats.loc[stats["genre"] == "Mecha"].iloc[0]
    assert bool(action["low_support"]) is False
    assert bool(mecha["low_support"]) is True


def test_dq_gate_warn_on_missing_year():
    raw = pd.DataFrame(
        {
            "title": ["A", "A"],
            "id": ["1", "2"],
            "follows": [10, 20],
            "rating_average": [9.0, 8.0],
            "rating_bayesian": [9.0, 8.0],
            "year": [2000, None],
            "rank": [1, 2],
            "status": ["ongoing", "completed"],
            "authors": [["x"], ["y"]],
            "related": [[], []],
        }
    )
    processed = raw.drop_duplicates("title").copy()
    processed["follows_group"] = ["Low", "Medium"][: len(processed)]
    # Keep both rows for processed path in this unit test.
    processed = raw.copy()
    processed["follows_group"] = ["Low", "Medium"]
    summary = validate_data_quality(raw, processed)
    assert summary["gate"] in {"pass", "warn"}
    assert summary["warning_count"] >= 1
