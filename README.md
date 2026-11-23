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

Use **Settings → Manage users → ＋ User** to create password-protected accounts.
Enter a separate username (1–80 ASCII letters and numbers, case-insensitively
unique), display name, optional email and phone, and select one or more roles.
For example, `Analyst42` is a username; the display name can contain spaces.
Administrators can edit usernames and check/uncheck multiple roles in Manage
users. The top of the left panel shows **Signed in as** with the username. The header
contains an **Active role** selector for the assigned roles. **Sign out** is in
Settings, in a separate session footer. Settings groups the data pipeline under
Workspace and places user management before role configuration under People &
permissions. Its account card shows the signed-in username and active role;
groups without permitted options are hidden. Small, thin chevron controls collapse and expand the left and right
panels. The chat is always visible. Side-panel handles remain available when
collapsed, and the selection survives reloads; old chat-collapse preferences are
ignored. A session uses only its selected role's
permissions. Switching roles immediately updates controls and API access without
signing out. The selection survives reloads and is independent for separate
sign-in sessions. If an active role is unassigned, the session falls back to an
assigned role. Accounts with no permissions in their current role can still
switch to another assigned role. `users:create` permits
creating default USER accounts; assigning other roles also requires
`roles:manage`. **Settings → Roles and permissions** creates custom named roles
and toggles their permissions. **Manage users** lists accounts; updating roles,
passwords or active status requires both `users:create` and `roles:manage`.
ADMIN can remove strictly lower-privilege accounts in **Manage users**. Removal
revokes sessions and deletes the account's saved chats; submitted feedback logs
are retained. Self-removal, other ADMIN accounts, and roles with equal or greater
permissions are protected. Uncheck **Active account** to immediately restrict
sign-in without deleting history, or assign a role with fewer permissions.
Default roles cannot be deleted. An assigned custom role must be reassigned
before deletion. The last active account with ADMIN membership cannot lose that membership, be
deactivated, or lose its effective `roles:manage` permission. These operations use serialized
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

The migration carries each existing primary role into a `profile_roles`
membership table and preserves account IDs, passwords, contacts, sessions and
conversations. Existing names receive a unique alphanumeric username by removing
non-alphanumeric characters and adding a numeric suffix for collisions (or using
`User` if no ASCII letters/numbers remain). The ADMIN username remains ADMIN.
Admins can see and edit migrated usernames in Manage users.

User API examples:

```json
{"name":"Jamie Smith","username":"Jamie42","password":"a-long-password-123","roles":["USER","REVIEWER"]}
```

`PATCH /api/users/{id}` accepts `username` and a `roles` array, which replaces the
complete assignment. At least one existing role is required; unknown or duplicate
roles are rejected atomically. Creating non-default role assignments and updating
accounts require `roles:manage` as well as `users:create`. The legacy singular
`role` input still replaces assignments with one role; do not send both fields.
User-management responses include `username`, assigned `roles`, their combined
`permissions` for administration, and the legacy primary `role`. Session responses
(`/api/profiles` and login) include `active_role` and permissions for that role
only; their `role` field reflects the active role. Authorization checks the
server-side session selection. `POST /api/auth/role` with `{"role":"USER"}`
switches to an assigned role. The endpoint requires sign-in, even when the current
role has no permissions. Admin-only actions require ADMIN to be selected.
The UI sends `X-Active-Role` on protected requests; stale tabs receive 409 and must
reload. Role switching closes open settings dialogs and reloads conversation data
under the newly selected role so unavailable scores and controls are removed.
Feedback records retain a snapshot of all assigned roles in addition to the
legacy role field. Email and phone recovery continue to use normalized contacts.

### Sign-in and password recovery

Sign in with a username, email, or phone number. Display names are separate
from sign-in usernames. Username sign-in ignores letter casing. For local phone numbers,
select the country using the searchable country/calling-code dropdown. Numbers
are validated and stored in E.164 format; email addresses are trimmed and
case-normalized. Recovery email and phone can be set while creating a user or
in **Settings → Manage users**. Each contact can belong to only one account.

**Forgot Password?** on the sign-in dialog has three steps: select email/phone,
enter the received six-digit code, and enter/confirm a new password of at least
12 characters. Codes expire after 10 minutes, allow five verification attempts,
and can be used once. Requesting another code invalidates previous codes and
verified reset grants across both contact methods. Completing a reset invalidates
all sessions. Changing recovery contacts or resetting a password administratively
also invalidates pending recovery codes. Recovery never reactivates disabled
accounts. Request responses use the same generic message for known and unknown
accounts. SQLite-backed limits allow five requests per contact and fifteen per
IP per 10 minutes, thirty verification/sign-in attempts per IP, and fifteen reset
completion attempts per IP. Rate-limit responses use HTTP 429 and Retry-After.

With no provider configured, the sign-in recovery dialog shows a development
banner and the backend console prints `PASSWORD RESET` with the destination
and code. Codes are never returned by an API. For email delivery, configure
`SMTP_HOST`, `SMTP_FROM`, optional `SMTP_USERNAME`/`SMTP_PASSWORD`, `SMTP_PORT`
(default 587), and `SMTP_TLS` (`starttls`, `ssl` or `plain`). For SMS, configure
`SMS_WEBHOOK_URL`, which receives a POST with `to` (E.164) and `message`; optional
`SMS_WEBHOOK_TOKEN` is sent as a Bearer token. These settings are forwarded by
Docker Compose; for native Uvicorn, export them in the server environment.
Provider errors are logged while the public response remains generic.

To explicitly clear accounts, chats, feedback, sessions and reset tokens and seed
one clean ADMIN (leaving embedded and staged reviews intact):

```bash
venv/bin/python scripts/reset_users.py --confirm RESET-USERS
# Set real recovery contacts when appropriate:
# ... --email admin@example.com --phone '+64211234567' --country NZ
```

The script prints a newly generated password once and verifies name/email/phone
sign-in. Default recovery contacts `admin@example.com` and `+12025550123` are demo
values for console-mode testing; replace them before configuring external delivery.
This destructive reset never runs automatically at startup.

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

ADMIN accounts with `pipeline:manage` can use **Settings → Data Pipeline Management → Reset knowledge bank**
to open an in-app confirmation dialog. Type `CONFIRM` and click **Reset knowledge
bank** to permanently remove all embedded reviews. The
reset shares the model lock with chat and ingestion, keeps the existing Chroma
collection usable, and refreshes filters. The displayed conversation and evidence
are cleared; saved historical chats remain available. New requests against an
empty index return an explicit empty-knowledge-bank response without model or
embedding calls. Staging data, source files, feedback
logs and saved chats remain; an intentionally empty index stays empty on restart.
A failed storage deletion may be partial; the error prompts a retry.

### Response feedback

Accounts with `feedback:submit` see thumbs up/down beneath completed responses,
including restored chats. Selecting a rating reveals an optional comment and
Submit and Cancel buttons. Cancel discards the draft and restores any previously
saved rating and comment. Failed/interrupted turns cannot receive feedback. Feedback is
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
| `DELETE /api/users/{id}` | ADMIN + `users:create`; remove strictly lower-privilege accounts |
| `PATCH /api/users/{id}` | `users:create` + `roles:manage`; username/roles/active/password updates |
| `GET/POST /api/roles`, `PUT/DELETE /api/roles/{name}` | `roles:manage` |
| `GET /api/pipeline` | `pipeline:manage`; staging preview |
| `POST /api/pipeline/stage` | `pipeline:manage`; validated JSON/CSV/XLSX staging |
| `POST /api/pipeline/query` | `pipeline:manage`; `{"sql":"SELECT ..."}` |
| `POST /api/pipeline/reset` | ADMIN + `pipeline:manage`; `{ "confirmation": "CONFIRM" }` |
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

