# Manga Analysis — AI Data Engineer Home Assignment

End-to-end data product that extracts top-rated manga from the free public [MangaDex API](https://api.mangadex.org/docs/), validates quality, writes reproducible serving artifacts, and presents insights in Streamlit.

**Product question:** Which manga attributes and genres are associated with community success (followers), and which genres look most promising after controlling for sample-size support?

## Selected API

| Item | Detail |
|------|--------|
| API | [MangaDex API](https://api.mangadex.org) |
| Auth | **Keyless** (no API key / paid access / private credentials) |
| Endpoints | `GET /manga`, `GET /statistics/manga`, `GET /manga/tag` |
| Constraints handled | Rate limits, retries/backoff on 429, required `User-Agent`, pagination |

Cached raw extracts live under `data/raw/` so reviewers can run offline if the API is unavailable.

## How to run locally

```bash
git clone <repository-url>
cd manga_analysis
python -m venv .venv
```

Activate:

```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

Install and run:

```bash
pip install -r requirements.txt
python etl.py
streamlit run app.py
```

Optional tests:

```bash
pytest -q
```

### Reviewer notes

1. Always activate `.venv` first. If `streamlit` is not found, use `python -m streamlit run app.py`.
2. `python etl.py` writes all serving artifacts. `app.py` **only reads** processed files (no model training in the UI).
3. First API extract can take a few minutes; later runs reuse `data/raw/top_1000_manga.json` when present.
4. Open the URL Streamlit prints (usually `http://localhost:8501`).

## Architecture (medallion-style)

```text
API  ->  data/raw/                 (bronze: immutable extract)
     ->  transform + DQ gate
     ->  data/processed/           (silver/gold serving artifacts)
     ->  app.py                    (read-only consumption)
```

### Serving contract (`data/processed/`)

| Artifact | Purpose |
|----------|---------|
| `manga_cleaned.json` / `.csv` | Cleaned manga grain (1 row = 1 title) |
| `genre_success_stats.csv` | Genre aggregates + success score |
| `data_quality_report.csv` | DQ issues |
| `model_metrics.json` | K-Fold metrics / confusion matrix / importances |
| `model_predictions.csv` | Actual vs predicted follower groups |
| `feature_importance.csv` | Grouped feature contribution |
| `run_manifest.json` | run_id, timings, DQ gate, artifact paths |

## Data model and ETL

### Extraction
- Top 1000 titles by rating sort (`order[rating]=desc`)
- Expand author/artist/related manga
- Batch statistics (follows, ratings)
- Persist raw JSON for reproducibility

### Transformation
- Deduplicate titles
- Normalize tags/authors to lists
- English title (`title_en`)
- `follows_group` bins + flags (`is_award_winning`, `is_new`, `related_count`)
- Genre-only aggregation (excludes format/theme tags like Award Winning)

### Analytics
- Follower-group model (RandomForest, Stratified K-Fold)
- **Leakage controls:** `follows`, `rank`, `follows_group` excluded from features
- Genre success score with **minimum support** (`n >= 20`) so tiny genres are not ranked as winners by default
- Explicit multi-label grain note: one manga can count in multiple genres

## Streamlit application

Tabs:

1. **Before processing** — raw extract
2. **After processing** — cleaned data under sidebar filters
3. **Data quality** — gate status + issue table (trust layer)
4. **Model evaluation** — served metrics/confusion matrix/importances
5. **Genre success** — recomputed from filtered manga set

Sidebar filters (status, demographic, language, year) apply at **manga grain**; genre metrics are recomputed from the filtered set.

## Data quality checks and gates

`validate_data_quality()` records issues and returns a gate:

| Gate | Meaning |
|------|---------|
| `pass` | No issues |
| `warn` | Warnings only; ETL continues and serves data |
| `fail` | Errors present; ETL aborts before writing serving trust path |

Checks include empty frames, duplicate titles, missing critical fields, invalid rating/follower ranges, unexpected status values, missing author expansions, and related IDs outside the sample.

## Assumptions and limitations

- Sample is MangaDex top-rated titles, not a random catalog sample (selection bias).
- Release timing uses `year`, not a full timestamp.
- Genre leaderboard is multi-label; support thresholds reduce cold-start distortion.
- Minority follower classes remain hard to predict under class imbalance.
- Related titles outside the top-1000 are expected orphans in referential checks.

## How AI was used

Full interaction log: `cursor_api_call_error_investigation.md`

AI assisted API design, debugging, modeling choices, dashboarding, and hardening (serve/ETL split, leakage controls, DQ gates). AI suggestions were reviewed and corrected where needed.

## What I would improve with more time

- Dated raw snapshots (`raw/YYYY-MM-DD/`) and incremental refresh via `updatedAt`
- Parquet serving layer and stricter schema contracts
- Selection-bias study vs random MangaDex sample
- Richer franchise-graph analytics for related titles

## Repository structure

```text
README.md
requirements.txt
serving_contract.py   # shared paths / genre constants
app.py
etl.py
tests/
data/raw/
data/processed/
ai_transcript/
```
