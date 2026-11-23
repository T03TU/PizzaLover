# PizzaLover verification

The web app uses the existing 50-review Chroma index and local Ollama models.
No review ingestion tests write to the production index.

## Automated checks

- `venv/bin/python -m unittest discover -s tests -v`: 64 passing tests.
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
embedding failure, confirmed ADMIN-only index reset and retained staging/chats.
Multi-role tests verify permission union, immediate revocation, last-admin
protection, secondary-role deletion safeguards, atomic invalid assignments,
alphanumeric and case-insensitive usernames, numeric username/phone selection,
and deterministic username migration without losing accounts. The dedicated
`tests/test_multirole_ui.cjs` browser regression creates users with multiple roles,
edits assignments and usernames, and signs in with the changed username.
Role-switching tests verify session persistence, independent sessions,
unassigned-role rejection, switching back from a role without permissions,
stale-tab guards, active-role removal fallback, and ADMIN-only operations.
`tests/test_role_switch_ui.cjs` verifies the visible username, role selector,
controls and API access after switches, persistence on reload, and mobile layout.
Panel checks verify edge handles remain usable after collapse, directional arrows,
all side-panel visibility combinations with chat always visible, the username at the top of the left panel, and
sign-out within Settings. Settings checks cover the account summary, grouped
workspace/access options, hidden groups for restricted roles and the session
footer; desktop and mobile previews were visually inspected.
Password recovery tests cover normalized email/phone sign-in, console delivery,
generic responses, expiration, resend, attempt limits, rate limits, single-use
grants, password confirmation and session revocation. Seeding tests verify
transactional user reset and immediate sign-in with each identifier.
A real persistent Chroma regression removes 1,002 reviews across multiple batches,
verifies empty retrieval after reopening, confirms a new query in an existing
chat cannot retrieve deleted evidence or invoke generation, and checks that new
data can be indexed afterward. Browser checks exercise the in-app reset dialog,
cancellation, incorrect confirmation text and a new question after reset.
User deletion tests verify privilege comparisons, account/chat deletion and session
revocation. Feedback tests verify ownership, completion, metadata, rating
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

The test server seeds `Admin` / `admin-password-123` only in temporary storage,
with `browser@example.com` and `+64211234567` for console-only recovery tests.
`tests/test_recovery_ui.cjs` additionally tests email/phone recovery end to end,
country search, incorrect codes, password confirmation, normalized sign-in and
unknown-account confirmation. Run it with `SERVER_LOG` pointing at the temporary
test server's captured console output; no code-reading test API is exposed.
The optional `tests/test_chat_ui.cjs` browser regression runs in headless Chrome
against `tests/ui_server.py`, which uses fake inference and temporary storage.
It verifies password sign-in, user creation, account isolation, permissions in
Settings, custom roles, pipeline staging/SQL/embedding, feedback persistence,
scorecard/feedback hiding for restricted roles, saved chat restoration/continuation,
citation reopening, interrupted query persistence, and all four side-panel visibility
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
