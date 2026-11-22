# PizzaLover verification

The web app uses the existing 50-review Chroma index and local Ollama models.
No review ingestion tests write to the production index.

## Automated checks

- `venv/bin/python -m unittest discover -s tests -v`: 39 passing tests.
- `node tests/test_sse_parser.cjs`: passes byte-by-byte UTF-8 chunks, split SSE
  frames, CRLF separators, evaluation heartbeat status, and termination events.
- `node --check static/app.js`: passes.
- Python compilation succeeds for the API, engine, CLI scripts, and tests.

The HTTP tests inject a deterministic in-memory engine through the real FastAPI
lifespan. They cover source-before-token-before-metrics ordering, metadata
filters, empty results, disabled evaluation, invalid requests, static assets,
CSV/JSON ingestion, duplicate IDs, history saving, golden CSV summaries, explicit
stream errors, and compatibility with Document inputs to the shared chain.
Temporary SQLite databases verify saved queries, answers, sources, metrics,
filters, restart persistence, authenticated account isolation, chat continuation and
deletion, failed/interrupted turns, and stale account requests from other tabs. Permission tests cover the six granular
permissions, custom role creation/revocation, score redaction, migration,
password/session handling and last-admin protection. Pipeline tests cover staging
without embedding, SQL isolation, atomic rejection, validation, persistence and
embedding failure. Feedback tests verify ownership, completion, metadata, rating
updates and durable storage.

The current feature checks use fake inference and temporary databases. Live
Ollama/model integration was not rerun for the RBAC, pipeline and feedback work.

## Earlier local integration checks

- Ollama reports `llama3.2` and `mxbai-embed-large` installed.
- Uvicorn serves `/` with HTTP 200.
- Metadata returns 50 reviews, all five franchises, and all five locations.
- A live question filtered to Georgies Pizza / East Auckland returns only
  matching reviews, with streamed text and matching inline citations.
- Unfiltered pepperoni queries emit citations, token chunks, four score fields,
  timings, and done. The save route writes the web session's history to
  `results/evaluation_history.json` and returns its record count and path.

An initial run of `scripts/evaluate_golden.py --limit 2` completed and exported
its report, confirming the CLI still runs with the shared prompt. Several judge
metrics hit the old concurrent evaluator's timeouts. Initial live scoring also
exposed invalid JSON from the small local judge. Judge calls now request native
JSON-schema-constrained output, and individual evaluation jobs run sequentially
with bounded retries. Updating the outdated instructor adapter resolved the
schema failures. The final unfiltered live query returned all four numeric
scores (faithfulness 0.5000, answer relevancy 0.8122, context precision 0.0000,
context utilization 0.0000), streamed matching feedback citations, and saved
one session record successfully. These values verify scoring, not a minimum
quality guarantee; local model quality varies by question. Missing metric
values are displayed explicitly rather than replaced with invented scores.
`pip check` reports no broken requirements.

To run the optional browser regression (Playwright must be installed):

```bash
# Terminal 1
venv/bin/python tests/ui_server.py
# Terminal 2
node tests/test_chat_ui.cjs
# Or set PLAYWRIGHT_PATH to a separate installation's playwright module path.
```

The test server seeds `Admin` / `admin-password-123` only in temporary storage.
The optional `tests/test_chat_ui.cjs` browser regression runs in headless Chrome
against `tests/ui_server.py`, which uses fake inference and temporary storage.
It verifies password sign-in, user creation, account isolation, permissions in
Settings, custom roles, pipeline staging/SQL/embedding, feedback persistence,
scorecard/feedback hiding for restricted roles, saved chat restoration/continuation,
citation reopening, interrupted query persistence, and all eight pane visibility
combinations at 1440, 1000, 850, and 390 pixels without horizontal overflow.
Pane visibility and the active conversation survive reloads. Desktop and phone
screenshots were also inspected. These checks exercise the actual UI and API;
they do not invoke or change the production review index or model.

The earlier two-case golden benchmark was rerun with the updated dependencies:
all 12 scoring jobs completed, all six metrics returned numeric values for
both cases, and the CSV export succeeded (about 327 seconds of evaluation).
The low faithfulness/context precision scores remain visible; successful
execution is not evidence that the model's answer quality is high.
The local test server also shut down cleanly after real evaluation and saving.
