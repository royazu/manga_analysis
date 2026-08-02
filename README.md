# Manga Analysis — AI Data Engineer Home Assignment

End-to-end data product that extracts top-rated manga from the free public [MangaDex API](https://api.mangadex.org/docs/), transforms it into analytical datasets, validates data quality, and presents insights in a Streamlit dashboard.

**Product question:** Which manga attributes and genres are most associated with commercial/community success (followers), and which genres look most promising based on rating, ranking, and follower metrics?

## Selected API

| Item | Detail |
|------|--------|
| API | [MangaDex API](https://api.mangadex.org) |
| Auth | **Keyless** (no API key, no paid access, no private credentials) |
| Main endpoints | `GET /manga`, `GET /statistics/manga`, `GET /manga/tag` |
| Constraints handled | Rate limiting (~5 req/s), required `User-Agent`, pagination (`limit` ≤ 100) |

Cached raw extracts are committed under `data/raw/` so a reviewer can run the project even if the live API is temporarily unavailable.

## How to run locally

After cloning the repository, use the standard flow below. No Docker, external database, cloud credentials, or manual data preparation are required.

```bash
git clone <repository-url>
cd manga_analysis
python -m venv .venv
```

Activate the virtual environment:

```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

Install dependencies and run:

```bash
pip install -r requirements.txt
python etl.py
streamlit run app.py
```

### Notes for reviewers

1. **Activate the venv before running Streamlit.** Inside an activated `.venv`, `streamlit` is on `PATH`. If you see `streamlit: command not found` (common with some system Python installs), use:

   ```bash
   python -m streamlit run app.py
   ```

2. **First ETL run without cache** may take a few minutes (API pagination + statistics + model training). Subsequent runs reuse `data/raw/top_1000_manga.json` when present.

3. Open the dashboard at the URL Streamlit prints (usually `http://localhost:8501`).

## Repository structure

```text
README.md
requirements.txt
app.py                 # Streamlit application
etl.py                 # Main ETL / analytics entry point
data/
  raw/                 # Raw API extracts (cached)
  processed/           # Analytical outputs + DQ report
ai_transcript/         # Full AI conversation transcript
```

Optional folders (`src/`, `tests/`) are not required for the current runnable solution.

## Data model and ETL

### Extraction (`etl.py`)

1. Health-check MangaDex (`result == "ok"`).
2. If no local cache: fetch top **1000** manga by Bayesian rating sort (`order[rating]=desc`), 100 per page.
3. Expand relationships: author, artist, related manga.
4. Batch-fetch statistics (`follows`, average/Bayesian rating).
5. Persist raw JSON to `data/raw/top_1000_manga.json` and a flat CSV to `data/processed/top_1000_manga.csv`.

### Transformation

- Deduplicate by title.
- Normalize tags to Python lists.
- Keep English title (`title_en`) alongside primary title.
- Bin followers into `follows_group`: Low / Medium / High / Very High.
- Engineer flags: `is_award_winning`, `is_new`.
- Aggregate **genre-only** tags (excludes format/theme tags such as Award Winning, Official Colored) into `data/processed/genre_success_stats.csv`.

### Analytics

- **Follower-group model:** Stratified 5-Fold RandomForest predicting `follows_group` from ratings, rank, year, demographics, status, language, and tags.
- **Feature contribution:** Grouped RandomForest importances (which field family drives predictions).
- **Genre success score:** Combines average followers, Bayesian rating, and average rank to rank genres by success likelihood.

## Streamlit application (`app.py`)

Four tabs for a business-facing review:

1. **Before processing** — raw extract preview, missing-value overview, follower distribution.
2. **After processing** — dedupe impact, new columns, `follows_group` breakdown.
3. **Model evaluation** — accuracy / precision / recall / F1 / ROC-AUC, confusion matrix, field contribution chart, classification report.
4. **Genre success** — ranked genres with success score and supporting metrics.

Heavy steps are cached so reloading the UI does not retrain the model every time.

## Data quality checks and validation

Implemented in `validate_data_quality()` and written to `data/processed/data_quality_report.csv`:

| Check | Purpose |
|-------|---------|
| Empty raw/processed frames | Hard failure signals |
| Duplicate titles (raw) | Identity / dedupe awareness |
| Missing `follows`, ratings, `year`, `rank` | Completeness |
| Negative followers | Invalid ranges |
| `rating_average` outside 0–10 | Invalid ranges |
| Unexpected `status` values | Category validation |
| Null `follows_group` | Binning / edge-case coverage |

Operational handling also includes:

- HTTP `raise_for_status()` on API calls.
- Explicit API health check using MangaDex `result` (not `status`).
- Request pacing (`REQUEST_PAUSE_S`) to respect rate limits.
- Offline fallback to cached raw data and fallback genre tag list if `/manga/tag` is unreachable.

## Assumptions and known limitations

- “Top 1000” is MangaDex’s rating-sorted list (Bayesian ranking), not an external bestseller chart.
- Release timing uses `year` (MangaDex does not expose a reliable full initial-release timestamp for all titles).
- A manga can belong to multiple genres; genre aggregates count titles in each matching genre.
- Follower bins are heuristic cut-points, not official MangaDex segments.
- Class imbalance (few Low / Very High titles) limits minority-class recall in the model.
- Related titles (spin-offs/sequels) are stored in raw data but not heavily featured in the dashboard charts.
- Live API rankings can drift over time; committed cache makes reviewer runs reproducible.

## How AI was used

AI (Cursor agent) was used throughout the assignment. The full interaction log is in:

`ai_transcript/conversation_log.txt`

Examples of usage:

- Interpreting MangaDex API docs and designing the extract strategy.
- Debugging ETL/runtime issues (`NameError`, wrong API health field, PATH/`streamlit` command, sklearn feature encoding).
- Iterating on analytics (K-Fold evaluation, genre success aggregation).
- Building the Streamlit dashboard and validating the reviewer run path.

AI output was reviewed and corrected when needed (e.g. checking `result` vs `status`, encoding features before KNN/RF, separating confusion-matrix evaluation from feature importance).

## What I would improve with more time

- Persist model artifacts and a richer DQ dashboard tab (pass/fail gates in UI).
- Add unit tests for preprocessing, genre filtering, and API response parsing.
- Time-based refresh policy for the raw cache and changelog of ranking movement.
- Richer product UX: filters by demographic/status/year, drill-down from genre to titles.
- Compare additional models and calibrate follower bins from data quantiles.

## Submission checklist (assignment alignment)

- [x] Python + Streamlit + free public API (MangaDex, keyless)
- [x] ETL extract → transform → validate → local store (`data/raw`, `data/processed`)
- [x] Business-facing Streamlit app (`app.py`)
- [x] Data quality checks and error handling
- [x] Git repository submission layout
- [x] AI transcript under `ai_transcript/`
- [x] Standard local run commands documented above
