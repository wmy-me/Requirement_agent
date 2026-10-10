# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (src-layout: package lives at src/requirement_agent)
pip install -e .          # or: uv sync   (uv.lock is committed)

# Migrate — raw SQL, ordered, MUST complete before the service starts
createdb requirement_agent
for f in migrations/0*.sql; do psql -d requirement_agent -f "$f"; done

# API + legacy frontend (:8888)
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8888 --reload

# Background worker (:8200)
.venv/bin/python -m requirement_agent.workers

# Tests (pytest; pythonpath = src + "." is configured in pyproject.toml)
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/unit/test_document_parser.py::test_name -q   # single test
```

Tests talk to a **real PostgreSQL** — there is no in-memory fake. `tests/conftest.py` sets
`API_AUTH_TOKEN=test-api-token` before `settings` is constructed; that is the only reason
authenticated clients work. Point `POSTGRES_DB` at a throwaway migrated database for test runs;
never let tests write to the business database.

There is no configured linter/formatter (`.ruff_cache`/`.mypy_cache` are gitignored but no
`[tool.ruff]`/`[tool.mypy]` exists). Type checking is on the frontend only.

### Frontend (`frontend/`)

```bash
cd frontend
npm run dev          # Vite dev server; proxies /api to VITE_API_ORIGIN (default http://127.0.0.1:8000)
npm run typecheck    # tsc -b
npm run build        # tsc -b && vite build  → frontend/dist
```

## Architecture

Layered, `src`-layout. `api` → `application` → `domain` / `infrastructure`; `workflows`
(LangGraph-style graphs) orchestrate `agents`/`skills`; `tools/` are plain callable methods that
go through the same application services as HTTP. Neither HTTP routes nor tools touch domain
logic directly.

### Two independent frontends, one route table each

- **Legacy** `static/app/*.html` + `static/app/js/` (vanilla JS), served at `/app`.
- **React workbench** `frontend/` (React + TS strict + Vite), served at `/app-next` from
  `frontend/dist`, mounted in `api/app.py`. Non-asset subpaths fall back to the SPA index so deep
  links survive hard refreshes.

Do not build a second route definition for either one. `/app` is kept as the fallback entry and
must keep working.

### Runtime credential injection

Auth is a bearer token (`API_AUTH_TOKEN`, an admin-compat entry; roles resolved in `api/auth.py`).
The token is **not** baked into the bundle and must never go into `VITE_*` or source. It is
injected per response as a `<meta name="ra-ui-token">` by replacing the `<!-- RA_UI_TOKEN_INJECT -->`
marker in the HTML, because CSP forbids inline `<script>`. Every HTML page needs the marker — a
page missing it is silently all-401.

### Snowflake IDs are strings

IDs are 63-bit snowflakes that exceed `Number.MAX_SAFE_INTEGER`. They are serialised to JSON
**strings** via `common/snowflake.to_sid`. Never `Number()`/`parseInt` an ID on the client, and
never emit a raw int where the contract says string — the two ends drifting apart is a
previously-shipped bug class (an id differed by 1 after `int(float())`, so retries hit the wrong
row). Note the asymmetry: `/api/v1/documents/{id}` takes the id as an **int** path param even
though the list returns it as a string.

### Analysis never writes requirements

The analysis pipeline writes only to `requirement_source.metadata` and sets
`processing_status=pending_review`. Formal writes to `requirement_master` /
`requirement_version` / `requirement_feature` happen **only** in the human-review transaction
(`ReviewService`). Do not add an agent-path write that skips review.

### Execution ownership (token / lease / deadline)

A run has an execution token, a heartbeat lease and a persisted absolute deadline
(`agent_run`, `application/run_tracking.py`, migration 028/029). Every write from a running
executor is **token-constrained**: a lease-lost or superseded executor must not write checkpoints,
append events, or publish its result. Heartbeat failure marks the lease lost and blocks further
model/tool calls. Business-visible publication is fenced by re-checking ownership immediately
before it. When touching any run/worker/chat path, preserve this — it is the main correctness
invariant in the codebase, and it is what most of `tests/integration/test_agent_run_tracking.py`,
`test_dynamic_chat_checkpoint.py` and `test_run_resume.py` exist to protect.

### Outbox

Channel ingest writes a source and returns immediately (Feishu requires a 3s response); an outbox
event drives analysis asynchronously. Document chunking/vectorisation also rides the outbox. The
consumer starts in the API `lifespan` and claims work with `FOR UPDATE SKIP LOCKED`, so multiple
instances are safe. Retries use backoff with a dead-letter terminal state.

### Version / feature model

`requirement_master` (current_version) → `requirement_version` (snapshot + `feature_changes`
jsonb) → `requirement_feature`. `feature_changes` is what makes `at_version=N` replay possible:
`features?at_version=N` returns the members **and content** as of that version, not just rows that
happened to exist. A partial unique index allows exactly one `status='current'` version per
requirement. `requirement_feature.module_key/module_name` (migration 014) is the real "module"
concept — it is **not** `business_domain`, and must not be inferred from the title. Features with
no module carry NULLs; surface that as "not provided" rather than substituting a domain.

### LLM provider and degradation

Provider is OpenAI-compatible (DeepSeek in the current `.env`). `httpx[socks]` is a declared
dependency because this environment egresses through a SOCKS proxy — without it every LLM and
embedding call silently degrades. Degraded analysis/risk results must be **marked** in metadata,
never presented as a confident result.

### Repository conventions

Comments explain *why* (often citing the incident or measurement that motivated the code), in
Chinese, and are intentionally dense — match that style. Migrations are append-only: to change an
object, `DROP ... IF EXISTS` and recreate it in a **new** numbered file; `006` is destructive
(vector dim 1536→4096, drops indexes).

## Further reading

`README.md` (setup, endpoints) · `migrations/README.md` (per-migration purpose) ·
`docs/agent_refactor_progress.md` and `docs/frontend_refactor_progress.md` (in-flight state,
what is deliberately unfinished) · `docs/Requirement_Agent_Frontend_Refactor_Plan.md` (frontend
direction and acceptance criteria).
