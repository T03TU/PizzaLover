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

Open **http://127.0.0.1:8000**. On first start, copy the **account setup token**
from the server console into the setup dialog and set an administrator password
(at least 12 characters). The token is single use and changes on restart until
setup is completed. Subsequent visits require an account name and password.
Keep one Uvicorn worker: resources, the model
lock, and evaluation history belong to that server session. API documentation
is available at `/docs`. Existing CLI commands remain available:

```bash
python main.py
python scripts/evaluate_batch.py
python scripts/evaluate_golden.py --limit 2
```

## Running with Docker

You can run PizzaLover anywhere using Docker, with two supported options:

### Mode 1: Connect to Host Ollama (Recommended)
Use this mode if Ollama is running on your host machine to take advantage of native GPU / Apple Metal acceleration without complex container passthrough:

```bash
# 1. Ensure required models are pulled on your host
ollama pull llama3.2
ollama pull mxbai-embed-large

# 2. Start the web container
docker compose up
```

Open **http://127.0.0.1:8000**. The app communicates with host Ollama via `http://host.docker.internal:11434`.

### Mode 2: Fully Dockerized (Web App + Ollama Container)
Use this mode to run both PizzaLover and Ollama entirely inside Docker:

```bash
# 1. Start both services
docker compose --profile with-ollama up -d

# 2. Pull models into the Ollama container
docker compose exec ollama ollama pull llama3.2
docker compose exec ollama ollama pull mxbai-embed-large

# 3. View logs
docker compose logs -f
```

### Standalone Docker Run (Without Compose)
```bash
# Build the image
docker build -t pizzalover .

# Run pointing to host Ollama
docker run -d --name pizzalover \
  -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/results:/app/results" \
  pizzalover
```


The sidebar filters indexed reviews by franchise, location, and minimum rating.
Each question retrieves up to three matching reviews. Answers stream first;
optional Ragas scoring follows. Click an inline feedback ID to highlight its
source, or click an earlier answer to restore that turn's evidence and scores.
Each question is independent; prior chat messages are not sent as model context.
New chat clears the displayed conversation. Conversations, evidence, filters and
turn metrics persist in SQLite for the signed-in account; session evaluation
aggregates remain until restart. Chat messages are not stored in browser storage.

Use **Add customer reviews** to upload UTF-8 CSV or Excel `.xlsx` files (up to 500 rows, under
2 MiB including multipart overhead). The required columns are `Feedback_ID`,
`Franchise`, `Location`, `Rating`, and `Comment`. Ratings must be integers 1–5;
IDs must start with `FB-` and contain letters, digits, or hyphens. Existing IDs
are rejected. The whole upload is validated before embedding/indexing. JSON
reviews can also be posted directly:

```bash
# Sign in as an account with pipeline:manage, keeping its session cookie.
curl -c /tmp/pizzalover-cookies http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"name":"Admin","password":"YOUR_PASSWORD"}'
curl -b /tmp/pizzalover-cookies http://127.0.0.1:8000/api/ingest \
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
command above; it uses password authentication and local sessions, and does not add external hosting.

### Permissions, accounts and migration

The six supported permissions are `chat:use`, `scorecards:view`,
`feedback:submit`, `users:create`, `pipeline:manage`, and `roles:manage`.
`USER` starts with the first three; `ADMIN` starts with all six. **Settings** is
available to every signed-in account. Its pipeline, role and user management
options are visible only with the corresponding permission. The API checks
permissions independently and returns 403 for authenticated, unauthorized
requests (401 for missing/expired credentials). Chat-only roles cannot obtain
scores from SSE, saved chats, or evaluation endpoints.

Use **＋ User** to create password-protected accounts. `users:create` permits
creating default USER accounts; assigning other roles also requires
`roles:manage`. **Settings → Roles and permissions** creates custom named roles
and toggles their permissions. **Manage users** lists accounts; updating roles,
passwords or active status requires both `users:create` and `roles:manage`.
Default roles cannot be deleted. An assigned custom role must be reassigned
before deletion. The last active ADMIN cannot be demoted or deactivated, and
`roles:manage` cannot be removed from ADMIN. These operations use serialized
SQLite transactions. Role changes affect existing sessions on their next request;
password resets and deactivation revoke sessions.

Existing profiles keep their IDs, names and saved chats. On the first migration,
the oldest profile becomes ADMIN and the remaining profiles become USER. The
one-time setup sets the password on that existing ADMIN (the setup name field is
ignored). That administrator then sets passwords for the other migrated accounts
in **Manage users**. Passwordless profile switching and automatic guest accounts
are replaced by sign-in. No profile or conversation data is deleted. Back up
`data/chat_history.sqlite3` before upgrading. Passwords use salted scrypt hashes;
sessions use opaque random cookies with HttpOnly/SameSite=Strict and server-side
expiry. HTTPS sets Secure cookies. Run behind HTTPS if exposing the service
beyond the trusted local machine.

### Pre-embedding data pipeline

Open **Settings → Data Pipeline Management** with `pipeline:manage`. Upload a CSV
or XLSX file into staging (column mapping is supported), inspect the table, and
run SQL to correct values before generating embeddings. The existing direct
upload and Google import also require `pipeline:manage`.

```sql
SELECT * FROM staging_reviews LIMIT 100;
UPDATE staging_reviews SET Franchise = trim(Franchise);
UPDATE staging_reviews SET Location = 'Auckland CBD' WHERE Location = 'Auck CBD';
```

The separate `data/staging.sqlite3` database contains only `staging_reviews`, with
the five review columns. SELECT, INSERT, UPDATE and DELETE are allowed. SQLite's
statement authorizer rejects access to other tables, schema changes, PRAGMA,
ATTACH, extension loading and unapproved functions; multiple statements are
rejected. SQL has length/execution limits and previews return at most 500 rows.
Query errors roll back changes. **Generate embeddings** validates all staged
reviews and checks indexed IDs before using the configured embedding model.
Successful embedding clears staging and refreshes filters. Staging edits and
embedding are serialized so the embedded data matches the edited snapshot.
Invalid data or model errors retain staging for correction. A model/storage
failure can partially write the vector index; if a retry reports indexed IDs,
remove those rows from staging before retrying. Embedding batches are limited
to 500 rows.

### Response feedback

Accounts with `feedback:submit` see thumbs up/down beneath completed responses,
including restored chats. Selecting a rating reveals an optional comment and
Submit button. Failed/interrupted turns cannot receive feedback. Feedback is
saved in `response_feedback` in `data/chat_history.sqlite3`, with rating, comment,
message ID (the saved turn ID), user ID, role at submission, and a UTC timestamp.
A user can update their rating/comment; one record per response/user is retained.
Feedback survives restarts and chat deletion for logging. The API verifies
response ownership and completion and derives identity/role/time on the server.

Additional API endpoints (all except auth status/setup/login require sign-in):

| Endpoint | Permission / behavior |
| --- | --- |
| `GET /api/auth/status`, `POST /api/auth/setup` | One-time console-token setup |
| `POST /api/auth/login`, `POST /api/auth/logout` | Start/end a browser session |
| `GET /api/profiles` | Current account and permissions |
| `GET/POST /api/users` | `users:create`; list/create accounts |
| `PATCH /api/users/{id}` | `users:create` + `roles:manage`; role/active/password updates |
| `GET/POST /api/roles`, `PUT/DELETE /api/roles/{name}` | `roles:manage` |
| `GET /api/pipeline` | `pipeline:manage`; staging preview |
| `POST /api/pipeline/stage` | `pipeline:manage`; validated JSON/CSV/XLSX staging |
| `POST /api/pipeline/query` | `pipeline:manage`; `{"sql":"SELECT ..."}` |
| `POST /api/pipeline/embed` | `pipeline:manage`; embed and clear staging |
| `POST /api/feedback` | `feedback:submit`; `{"message_id":"UUID","rating":"thumbs_up","comment":"optional"}` |

### Verification

```bash
python -m unittest discover -s tests -v
node --check static/app.js  # optional syntax check when Node is installed
node tests/test_sse_parser.cjs
```

Tests inject an in-memory engine and exercise real FastAPI routes/lifespan,
SSE ordering, filters, disabled evaluation, invalid input, CSV/JSON ingestion,
duplicate handling, saved conversations, role migration, permission denials, admin
lockout, SQL isolation, staging/embedding, feedback ownership, golden summaries,
and legacy chain inputs.
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

## Troubleshooting

### Port already in use (`[Errno 98] address already in use`)

If Uvicorn or Docker fails to start because port 8000 is occupied (`[Errno 98] error while attempting to bind on address ('127.0.0.1', 8000)`):

1. **Identify the process using port 8000**:
   ```bash
   lsof -i :8000
   # or
   fuser 8000/tcp
   ```

2. **Terminate the process**:
   ```bash
   kill <PID>
   # or kill whatever is listening on port 8000 directly:
   fuser -k 8000/tcp
   ```

3. **Alternative — run on a different port**:
   - **Direct Uvicorn**:
     ```bash
     uvicorn api.server:app --host 127.0.0.1 --port 8001
     ```
   - **Docker Compose**: Change the host port mapping in `docker-compose.yml`:
     ```yaml
     ports:
       - "8001:8000"
     ```
   Then access the app at `http://127.0.0.1:8001`.

