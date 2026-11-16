# Pizza Feedback RAG Tutorial

This project implements a local retrieval-augmented generation pipeline for pizza feedback data, with evaluation tooling built around Ollama and Ragas.

## Standard project layout

- `src/` — application modules and reusable package code
- `scripts/` — execution entrypoints for evaluation and generation workflows
- `tests/` — test fixtures and regression data
- `data/` — raw datasets, generated artifacts, and Chroma persistence
- `results/` — exported evaluation outputs and summaries
- `docs/` — project documentation

## Quick start

1. Install dependencies:
   `pip install -r requirements.txt`
2. Start Ollama and ensure the required models are available.
3. Run the application entrypoints from `scripts/` or the compatibility wrappers at the project root.

## Notes

The project keeps canonical code under `src/` and uses the root-level files as compatibility shims so existing scripts continue to work while the repository follows a conventional Python directory layout.

## PizzaLover web app

PizzaLover adds a local FastAPI server and a responsive HTML/CSS/JavaScript
interface. There is no Node build step or hosted inference dependency.
Ragas/Chroma usage telemetry and hosted LangSmith tracing are disabled.

```bash
source venv/bin/activate
pip install -r requirements.txt
ollama pull llama3.2
ollama pull mxbai-embed-large
# Start `ollama serve` separately if the Ollama service is not already running.
uvicorn api.server:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Keep one Uvicorn worker: resources, the model
lock, and evaluation history belong to that server session. API documentation
is available at `/docs`. Existing CLI commands remain available:

```bash
python main.py
python scripts/evaluate_batch.py
python scripts/evaluate_golden.py --limit 2
```

The sidebar filters indexed reviews by franchise, location, and minimum rating.
Each question retrieves up to three matching reviews. Answers stream first;
optional Ragas scoring follows. Click an inline feedback ID to highlight its
source, or click an earlier answer to restore that turn's evidence and scores.
Each question is independent; prior chat messages are not sent as model context.
New chat clears the displayed conversation, while server evaluation history
remains until the server restarts. Chat messages are not stored in browser storage.

Use **Add customer reviews** to upload UTF-8 CSV or Excel `.xlsx` files (up to 500 rows, under
2 MiB including multipart overhead). The required columns are `Feedback_ID`,
`Franchise`, `Location`, `Rating`, and `Comment`. Ratings must be integers 1–5;
IDs must start with `FB-` and contain letters, digits, or hyphens. Existing IDs
are rejected. The whole upload is validated before embedding/indexing. JSON
reviews can also be posted directly:

```bash
curl http://127.0.0.1:8000/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"Feedback_ID":"FB-051","Franchise":"Sals","Location":"Downtown","Rating":5,"Comment":"Great pepperoni."}'
```

The API exposes:

| Endpoint | Behavior |
| --- | --- |
| `POST /api/chat/stream` | JSON question, filters, eval_enabled; SSE citations → token chunks → metrics → done. Errors emit error → done. |
| `GET /api/metadata/options` | Distinct indexed franchises/locations and document count. |
| `POST /api/ingest` | Single review JSON, JSON array, or multipart CSV/XLSX in the `file` field. |
| `GET /api/evaluation/summary` | Session mean/min/max/count plus the latest golden report aggregates. |
| `POST /api/evaluation/save` | Save session history to `results/evaluation_history.json`, replacing the file. |

Storage follows the existing repository layout: `data/chroma_db`,
`data/raw/pizza_feedback_50_entries.csv`, and reports under `results/`.
The legacy `chrome_langchain_db` is reused if the canonical database is absent;
review ingestion also supports the legacy `test-dataset` CSV fallback. Existing
collections retain their index distance; newly created collections use cosine.
`OLLAMA_BASE_URL`, `LLM_MODEL`, `EMBEDDING_MODEL`, and `RETRIEVER_K` retain their
configuration overrides. Default retrieval uses three reviews. The shared
prompt enforces review grounding and citations, and warns against interpreting
a retrieved sample as a complete ranking. Model adherence is still probabilistic.

Generation, embedding ingestion, and evaluation share an async lock to avoid
concurrent model workloads. Evaluation runs off the event loop after answer
streaming; long evaluations send SSE heartbeat comments. The live evaluator also runs
its individual metrics sequentially using Ollama’s schema-constrained JSON output, with a 300-second per-operation timeout
and at most two retries, to reduce contention on local hardware. Missing/failed metric
scores display as unavailable. The UI can stop reading a response; an already
running synchronous judge finishes before another model workload starts.
The app is intended for a trusted local machine and binds to loopback in the
command above; it does not add authentication or external hosting.

### Verification

```bash
python -m unittest discover -s tests -v
node --check static/app.js  # optional syntax check when Node is installed
node tests/test_sse_parser.cjs
```

Tests inject an in-memory engine and exercise real FastAPI routes/lifespan,
SSE ordering, filters, disabled evaluation, invalid input, CSV/JSON ingestion,
duplicate handling, history saves, golden summaries, and legacy chain inputs.
They do not require Ollama or modify the real review index.

### Google pizza review ingestion

Set `GOOGLE_PLACES_API_KEY` in the server environment and enable **Places API (New)**
with billing in that Google Cloud project. Restart the server, open **Import Google
reviews**, enter a two-letter country code (e.g. NZ, AU, US, GB), optionally enter a
region/city, choose 5–20 places, and import. The key stays on the server.

`POST /api/ingest/google` accepts
`{"country_code":"NZ","region":"Auckland","max_places":10}`.
It makes one paid Text Search request (including review fields); no pagination or
country-wide completeness is promised. Country address components are checked;
region text guides ranking and is not a strict geographic boundary. Google returns
at most five relevance-ranked reviews per place. No-text and schema-invalid rows
are skipped. Results report added reviews, duplicates, matched places, and skipped
rows. Repeated imports skip stable review IDs; they do not update existing reviews.

The existing five-column schema is preserved: `Feedback_ID` is a stable `FB-G-`
hash of Google's review identity, `Franchise` is the business display name,
`Location` is its formatted address, `Rating` is the individual review's stars,
and `Comment` is its original text. Independent restaurants are supported.
Business names/addresses beyond the existing 100-character limits are skipped.
New reviews persist in Chroma and become available to the existing filters/chat.

Google's standard Places terms restrict storing/indexing review content. Use
persistent ingestion only where your Google agreement permits this use; an API
key alone does not grant those rights. This importer does not retain author
attribution and is not a Google review display widget. See
[Places policies](https://developers.google.com/maps/documentation/places/web-service/policies)
and [Text Search documentation](https://developers.google.com/maps/documentation/places/web-service/text-search).

Excel uploads read the first worksheet with the same five column headers as CSV.
Use plain values rather than formulas. The entire batch is validated before
embedding; duplicate IDs or invalid rows reject the upload. Successful uploads
are embedded with the configured Ollama embedding model and persisted in Chroma;
franchise/location filters refresh automatically. Install updated dependencies
with `pip install -r requirements.txt` to enable Excel support.

If an uploaded CSV or XLSX has different column names (or extra columns), a
**Map review columns** dialog asks which source column supplies each of the five
schema fields. Sample values help identify the right columns. All fields must
map to distinct source columns; extra columns are ignored. Canceling stores
nothing. Mapping renames fields only: External file IDs are automatically converted to stable schema IDs; ratings
must be 1–5, and all existing validation/duplicate checks still apply.
`POST /api/ingest/preview` accepts a multipart `file` and returns columns, up to
three sample rows, row count, and `requires_mapping`, without embedding. To import
custom columns, send the original file to `/api/ingest` with a multipart `mapping`
field containing a JSON object of schema-field names to source-column names.

File uploads preserve valid `FB-` IDs of at most 64 characters. Other nonempty
text or integer IDs (including long Google review IDs) become deterministic
`FB-EXT-` hashes, so repeated imports retain duplicate detection. Original IDs
are not truncated or added as extra schema fields. Missing IDs still fail
validation. This normalization applies to CSV/XLSX uploads, including mapped
columns; the JSON API retains strict schema validation.

CSV/XLSX rows whose mapped `Comment` is empty, whitespace-only, or missing are
skipped before embedding. Upload results report `skipped_blank_comments` and
`skipped_reviews` (one-based review positions, excluding the header), and the UI
shows these counts and positions. If all comments are blank, the upload is
rejected with a prompt to check the Comment mapping. Other schema errors still
reject the batch. JSON submissions retain strict required-comment validation.
