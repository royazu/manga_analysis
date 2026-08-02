"""Shared serving paths and analytics constants (ETL writes; app reads)."""

from pathlib import Path

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")

RAW_PATH = RAW_DIR / "top_1000_manga.json"
CLEANED_JSON_PATH = PROCESSED_DIR / "manga_cleaned.json"
CLEANED_CSV_PATH = PROCESSED_DIR / "manga_cleaned.csv"
GENRE_STATS_PATH = PROCESSED_DIR / "genre_success_stats.csv"
DQ_REPORT_PATH = PROCESSED_DIR / "data_quality_report.csv"
MODEL_METRICS_PATH = PROCESSED_DIR / "model_metrics.json"
MODEL_PREDICTIONS_PATH = PROCESSED_DIR / "model_predictions.csv"
FEATURE_IMPORTANCE_PATH = PROCESSED_DIR / "feature_importance.csv"
RUN_MANIFEST_PATH = PROCESSED_DIR / "run_manifest.json"

GENRE_MIN_COUNT = 20

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
