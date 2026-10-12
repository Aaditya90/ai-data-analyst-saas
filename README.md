# AI Data Analyst SaaS Platform

Production-grade, workspace-based AI Data Analyst platform. Built **phase-wise** —
each phase is merged into `main` only after review, so `main` is always in a
deployable state.

## Phase Status

| # | Phase | Status |
|---|-------|--------|
| 1 | Foundation (monorepo, workspace-aware schema, docker dev env) | ✅ Complete |
| 2 | Authentication (Clerk + workspace RBAC) | ✅ Complete |
| 3 | Data Ingestion (file upload + DB connectors) | ✅ Complete |
| 4 | Data Cleaning Engine (issue detection + lineage) | ✅ Complete |
| 5 | EDA Engine (profiling, correlations, chart suggestions) | ✅ Complete |
| 6 | Dashboard Builder (drag-and-drop widgets) | ✅ Complete |
| 7 | AI Query Engine (NL → safe SQL) | ✅ Complete |
| 8 | AI Insights (statistical detection + AI narration) | ✅ Complete |
| 9 | AI Dashboard Generator (one-click, AI-curated) | ✅ Complete |
| 10 | ML & Forecasting / AutoML | ✅ Complete |
| 11 | Reports (PDF/PPT) | ✅ Complete |
| 12 | Team Collaboration | ✅ Complete |
| 13 | Version History | ✅ Complete |
| 14 | Billing | ✅ Complete |
| 15 | Security | ✅ Complete |
| 16 | Observability | ✅ Complete |
| 17 | Testing | ✅ Complete |
| 18 | Deployment | ✅ Complete |
| 19 | Enterprise Features | ✅ Complete |

> Rule: only one phase is active at a time. A phase is not started until the
> previous one has been merged and the next phase is explicitly requested.

## Architecture Overview

```
Organization (billing entity)
  └── Workspace(s)              <- isolation boundary for data/dashboards/teams
        ├── Members (role-scoped: owner/admin/editor/viewer)
        ├── Datasets
        ├── Dashboards
        └── Reports
```

Every data-owning table carries `workspace_id`. This is the hard isolation
boundary — enforced both at the application layer (query filters) and at the
database layer (Postgres Row-Level Security), so a bug in one query can never
leak another workspace's data.

## Monorepo Layout

```
ai-data-analyst-saas/
├── apps/
│   ├── web/                # Next.js frontend
│   └── api/                # FastAPI backend
│       ├── app/
│       │   ├── core/        # config, db session, security helpers
│       │   ├── models/      # SQLAlchemy models (workspace-aware)
│       │   └── api/         # route handlers
│       └── alembic/         # DB migrations
├── infra/
│   └── docker/              # local dev container configs
├── docker-compose.yml
└── .env.example
```

## Tech Stack (Phase 1)

- **Frontend**: Next.js 14 (App Router), TypeScript, Tailwind
- **Backend**: FastAPI, SQLAlchemy 2.0, Alembic (migrations), Pydantic v2
- **Database**: PostgreSQL 16
- **Cache/Queue backbone**: Redis 7 (in active use since Phase 5, as a
  response cache for EDA/query/insights — no job queue yet)
- **Object storage**: MinIO (S3-compatible, for local dev)
- **Dev orchestration**: Docker Compose

## Getting Started

### 1. Environment setup
```bash
cp .env.example .env
```

### 2. Start infrastructure (Postgres, Redis, MinIO)
```bash
docker compose up -d
```

### 3. Backend setup
```bash
cd apps/api
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# run migrations
alembic upgrade head

# start API
uvicorn main:app --reload --port 8000
```

Visit `http://localhost:8000/health` — should return `{"status": "ok"}`.
Visit `http://localhost:8000/docs` for the auto-generated API docs.

### 4. Frontend setup
```bash
cd apps/web
npm install
npm run dev
```

Visit `http://localhost:3000`.

## What Phase 1 Delivers

- Monorepo skeleton (`apps/web`, `apps/api`) that all future phases build on
- Workspace-aware database schema:
  - `organizations` (billing entity)
  - `workspaces` (isolation boundary)
  - `users`
  - `workspace_members` (role-scoped membership: owner/admin/editor/viewer)
- Alembic migration producing the above schema, with Postgres Row-Level
  Security policies scaffolded on `workspaces`-owned tables
- Health-check endpoint + basic FastAPI app structure (`core/config.py`,
  `core/database.py`) that later phases (ingestion, dashboards, etc.) will
  plug routes and models into
- Docker Compose dev environment (Postgres, Redis, MinIO)
- `.env.example` documenting every config value the system will need going
  forward (auth, storage, AI provider keys — placeholders for now)

## What Phase 1 Deliberately Does NOT Include

No auth, no data ingestion, no AI calls — those are Phases 2–3 onward. Phase 1
is only the ground every later phase stands on. Keeping it minimal is
intentional: it makes review and merge trivial.

## What Phase 2 Delivers

- **Clerk integration** for identity — the frontend uses Clerk's prebuilt
  `<SignIn>`/`<SignUp>` components and `middleware.ts` protects every route
  except those two by default.
- **Backend JWT verification** (`app/core/security.py`) — verifies the
  Clerk session token on every API request against Clerk's JWKS, with no
  dependency on a specific Clerk SDK version (plain PyJWT + PyJWKClient).
- **`get_current_user` dependency** (`app/api/deps.py`) — every protected
  route depends on this; it verifies the token and mirrors the Clerk
  identity into our local `users` table on first sight.
- **`require_workspace_role(...)` dependency** — the workspace-scoped RBAC
  gate every future phase's workspace-owned routes should use. Returns 404
  (not 403) for non-members, so workspace existence isn't leaked to
  outsiders; returns 403 when the member's role doesn't meet the minimum.
- **Clerk webhook handler** (`POST /webhooks/clerk`) — keeps the local
  `users` table in sync with Clerk (create/update/delete) via
  svix-verified signatures, so users are mirrored even before their first
  API call.
- **Minimal organizations/workspaces routes** — just enough
  (`POST /organizations`, `POST /workspaces`, `POST /workspaces/{id}/members`)
  to create real data and prove the RBAC dependency actually blocks/allows
  correctly. Full workspace management (invites, settings) is Phase 12.

## Testing Phase 2 (Requires a Real Clerk Account)

Unlike Phase 1, this phase can't be fully verified without your own Clerk
project — JWT verification is deliberately real, not mocked.

1. Create a free account at [clerk.com](https://clerk.com) and a new
   application.
2. From the Clerk dashboard, copy into `apps/web/.env.local` (copy from
   `.env.local.example`):
   - `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
   - `CLERK_SECRET_KEY`
3. From **API Keys -> Advanced**, copy into your root `.env`:
   - `CLERK_JWKS_URL`
   - `CLERK_ISSUER`
   - `CLERK_SECRET_KEY` (same value as above — used here for webhook
     signature verification)
4. `pip install -r requirements.txt` again (adds PyJWT, svix, httpx) and
   `npm install` in `apps/web` (adds `@clerk/nextjs`).
5. Start both `uvicorn` and `npm run dev`, then visit
   `http://localhost:3000` — you should be redirected to `/sign-in`.
6. Sign up. You should land on `/dashboard`, which calls the API's `/me`
   and `/workspaces` — confirming the token round-trip works end-to-end.
7. Optional: run `python tests/test_rbac.py` from `apps/api` to check the
   role-hierarchy logic in isolation (no Clerk account needed for this
   part).

Webhook testing (`/webhooks/clerk`) requires a public URL — use a tool like
`ngrok` locally, or skip it for now and rely on the `get_current_user`
just-in-time creation path, which covers local dev fine.

## What Phase 3 Delivers

- **Dataset / DatasetVersion model** — a Dataset is the logical entity
  ("Q3 Sales"); each ingestion creates an immutable `DatasetVersion` with
  its own cached schema snapshot (`schema_json`). This is deliberate
  version-from-day-one design: Phase 4 (Data Cleaning) writes new versions
  rather than mutating old ones, and Phase 13 (Version History) has
  something to show from the start instead of a retrofit.
- **File upload pipeline** (`POST /workspaces/{id}/datasets/upload`) —
  accepts CSV/TSV/Excel/JSON, validates size and parseability before
  committing anything, stores the raw file in object storage (MinIO
  locally / S3 in prod), and infers a schema with pandas
  (`app/services/schema_inference.py`).
- **DB connectors** (`app/services/db_connector.py`) — Postgres and MySQL
  for now, built generically on SQLAlchemy's inspector so a third
  SQL-based connector is mostly a driver + one line, not a new module.
  Connections are read-only by construction (`inspect()` + `SELECT`
  only — no arbitrary SQL execution path exists here at all) and
  credentials are Fernet-encrypted at rest (`app/core/crypto.py`).
- **Schema uniformity** — file uploads and DB-connector snapshots produce
  the identically-shaped `schema_json`
  (`[{name, inferred_type, nullable, sample_values}]`), so Phase 5 (EDA)
  and Phase 6 (Dashboard Builder) can treat both sources the same way.
- **Dataset preview** (`GET /workspaces/{id}/datasets/{id}/preview`) —
  re-reads the stored file for a row preview, capped by
  `DATASET_PREVIEW_ROW_LIMIT`.
- **Frontend**: a `/datasets` page — workspace picker, drag-in file
  upload, and a list of datasets with row/column counts and status.

## What Phase 3 Deliberately Does NOT Include

- **Snowflake/BigQuery connectors** — noted in the architecture but not
  implemented; the `ConnectionType` enum and `db_connector.py`'s
  driver-map pattern make adding them later straightforward.
- **Async/background ingestion** — uploads are parsed synchronously in the
  request. `DatasetVersionStatus` already has PENDING/PROCESSING states
  ready for when this moves to a Celery job (Phase 10).
- **Data cleaning or validation beyond "does it parse"** — garbage *rows*
  (wrong types, nulls, duplicates) are Phase 4's job; this phase only
  rejects garbage *files* (wrong format, empty, unparseable).

## Testing Phase 3

1. `pip install -r requirements.txt` again (adds boto3, pandas, openpyxl,
   pymysql) in `apps/api`.
2. Generate a `DATA_ENCRYPTION_KEY` and add it to `.env`:
   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. `alembic upgrade head` — adds `data_connections`, `datasets`,
   `dataset_versions`.
4. Restart `uvicorn`, sign in via the frontend, create a workspace if you
   haven't (`POST /organizations` then `POST /workspaces` — see Phase 2's
   README section), then visit `/datasets` and upload a CSV.
5. Confirm the dataset appears with the correct row/column count, and that
   `GET /workspaces/{id}/datasets/{id}` returns the inferred schema.
6. Optional — DB connector: `POST /workspaces/{id}/connections` with a
   **read-only** Postgres/MySQL role's credentials, then
   `GET .../connections/{id}/tables` and
   `POST .../connections/{id}/datasets` to snapshot a table.

## What Phase 4 Delivers

- **Issue detection** (`app/services/data_cleaning.py::detect_issues`) —
  profiles a dataset version for missing values, fully-duplicate rows,
  numeric outliers (1.5x IQR), and untrimmed whitespace. Each issue comes
  with a *suggested* operation — these are rule-based heuristics, not an
  LLM call. Real AI-driven suggestions (reading column semantics, deciding
  *why* a value looks wrong) are Phase 8's job; this phase makes sure
  there's a correct, well-labeled foundation for that to build on.
- **Human-in-the-loop cleaning** — `POST .../clean` never runs
  automatically; it requires the exact list of operations the caller wants
  applied (typically the frontend's pre-checked suggestions, user-editable
  before submitting). Supported operations: `drop_duplicate_rows`,
  `drop_null_rows`, `fill_nulls` (mean/median/mode/constant),
  `remove_outliers` (IQR), `trim_whitespace`, `cast_type`.
- **Dataset lineage, started here** — cleaning never mutates a version; it
  creates a new Dataset + DatasetVersion with `parent_version_id` pointing
  at the source version and `transformations_applied` logging exactly what
  ran. `GET .../lineage` walks that chain back to the raw upload. Starting
  this in Phase 4 (the first phase that *derives* data) avoids the costly
  retrofit a later phase would otherwise need.
- **Real unit tests** — `apps/api/tests/test_data_cleaning.py` runs
  standalone (`python tests/test_data_cleaning.py`, no DB/Clerk needed)
  and actually exercises the cleaning logic, not just import/syntax checks.
  10 tests, all passing.
- **Frontend**: a `/datasets/[id]/clean` page — lists detected issues with
  severity, lets the user uncheck anything, and applies the rest.

## What Phase 4 Deliberately Does NOT Include

- **Cleaning DB-connector datasets** — would mean writing back to a
  customer's database, which is out of scope and risky for an ingestion
  tool. Only file-upload datasets can be cleaned in this phase.
- **Auto-applied cleaning** — every operation requires explicit approval,
  by design (see `app/api/cleaning.py` module docstring).
- **Async cleaning jobs** — like Phase 3's uploads, cleaning runs
  synchronously in the request. Fine for the file sizes this phase
  targets; moves to Celery in Phase 10 alongside everything else that
  needs background execution.

## Testing Phase 4

1. No new Python dependencies — this phase only adds code, not packages.
2. `alembic upgrade head` — adds `parent_version_id` and
   `transformations_applied` to `dataset_versions`.
3. Optional but recommended: `cd apps/api && python tests/test_data_cleaning.py`
   — runs the 10 unit tests with no server/DB needed, confirms the
   cleaning logic itself is correct before you test through the API.
4. Restart `uvicorn`, upload a CSV with some messy data (nulls, duplicate
   rows, an obvious outlier) via `/datasets`, then click "Clean →" on it.
5. Review the detected issues, apply a few, and confirm a new dataset
   appears in the list.
6. Call `GET /workspaces/{id}/datasets/{cleaned_dataset_id}/lineage` and
   confirm it shows both the raw and cleaned versions in order.

## What Phase 5 Delivers

- **Column profiling** (`app/services/eda.py::compute_column_summaries`) —
  type-aware stats per column: numeric gets min/max/mean/median/std/
  quartiles, categorical gets top-10 value counts, datetime gets a range.
  Every column also reports null count/percentage and unique count.
- **Correlation analysis** — Pearson correlation across every pair of
  numeric columns, sorted by strength so the strongest relationships surface
  first.
- **Chart suggestions** — rule-based (numeric → histogram, low-cardinality
  categorical → bar, datetime+numeric → line, categorical+numeric →
  grouped bar, two numeric → scatter). Same honesty note as Phase 4's
  cleaning suggestions: this is heuristics from column types, not an LLM
  reasoning about what the data means — that's Phase 9 (AI Dashboard
  Generator), which will use this phase's output as one input.
- **Real caching, not just a TODO** — this is the first phase to actually
  use Redis (present since Phase 1's docker-compose but unused until now).
  Cache key is the immutable `version_id`, so there's no invalidation logic
  to get wrong — a version's profile is valid forever once computed. Pass
  `?refresh=true` to force recomputation.
- **9 passing unit tests** (`tests/test_eda.py`) — verified against actual
  correlated sample data during this build, not just syntax-checked.
- **Frontend**: a `/datasets/[id]/eda` page — column cards with inline
  stats and CSS-bar distributions (no charting library added; kept the
  frontend dependency footprint from Phase 2 unchanged), a correlation
  list, and the chart suggestions.

## What Phase 5 Deliberately Does NOT Include

- **EDA on DB-connector datasets** — same file-upload-only limitation as
  Phases 3-4's preview/cleaning endpoints; lifting it is a matter of
  reading through `db_connector.py` instead of object storage, deferred
  until a phase actually needs it.
- **A real charting library** — the frontend renders stats and simple CSS
  bars rather than pulling in a charting dependency. Phase 6 (Dashboard
  Builder) is where real interactive charts belong.
- **Profiling on cleaned/derived versions specifically** — works on any
  file-upload version regardless of whether it's a root or a Phase 4
  cleaned version; nothing version-type-specific here.

## Testing Phase 5

1. No new Python dependencies — `redis` has been in requirements.txt since
   Phase 1, just unused until now.
2. No new migration — Phase 5 doesn't touch Postgres at all, only Redis.
3. Confirm Redis is reachable: `docker compose ps` should show
   `ai-analyst-redis` healthy (it has been since Phase 1).
4. Optional: `cd apps/api && python tests/test_eda.py` — runs the 9 unit
   tests standalone.
5. Restart `uvicorn`, go to `/datasets`, click "EDA →" on any ready
   file-upload dataset.
6. Reload the page — response should come back noticeably faster and show
   `cached: true` in the summary line, confirming Redis caching is working.
7. Click "Recompute" to confirm the cache-bypass path also works.

## What Phase 6 Delivers

- **Dashboard + DashboardWidget models** — a dashboard is a named canvas;
  each widget stores its grid position (`x/y/w/h`), a `widget_type`
  (chart/table/kpi/text), and a `config_json` whose shape depends on the
  type. JSONB config means adding a fifth widget type later doesn't need a
  migration.
- **Aggregation service** (`app/services/aggregation.py`) — computes what
  a widget should render (`compute_kpi`, `compute_table`, `compute_chart`)
  from a dataset's DataFrame + the widget's config. Kept separate from the
  API layer, same pattern as Phase 4/5's services, with 9 passing unit
  tests exercising real grouped aggregations.
- **Widget data endpoint is separate from widget CRUD** — moving/resizing
  a widget (frequent, drag-and-drop) only ever touches
  `dashboard_widgets` rows; `GET .../widgets/{id}/data` is the only path
  that re-reads the underlying dataset file, and only on load/refresh.
- **Frontend drag-and-drop canvas** — `react-grid-layout` powers the
  `/dashboards/{id}` page: drag to move, resize from the corner, position
  changes persist via `PATCH` on drag/resize stop. An inline "Add Widget"
  form picks widget type, dataset, and columns (populated from the
  dataset's schema, fetched via Phase 3's `GET /datasets/{id}`).
- Widget rendering stays consistent with Phase 5's approach — CSS bars for
  charts and category distributions, no new charting library added.

## What Phase 6 Deliberately Does NOT Include

- **Widgets backed by DB-connector datasets** — same file-upload-only
  limitation carried from Phases 3-5's data-reading endpoints.
- **Dashboard sharing/permissions beyond workspace RBAC** — every
  workspace member with Viewer+ can see all dashboards in it; per-dashboard
  sharing is Phase 12 (Team Collaboration) territory.
- **AI-generated dashboards** — this phase is the manual builder;
  Phase 9 (AI Dashboard Generator) uses these same
  Dashboard/DashboardWidget models as its output target, so building this
  phase's data model correctly now avoids a rewrite there.

## Testing Phase 6

1. No new Python dependencies — this phase only adds code.
2. `npm install` in `apps/web` again (adds `react-grid-layout`).
3. `alembic upgrade head` — adds `dashboards` and `dashboard_widgets`.
4. Optional: `cd apps/api && python tests/test_aggregation.py` — runs the
   9 unit tests standalone.
5. Restart `uvicorn` and `npm run dev`, go to `/dashboards`, create one.
6. Click into it, "+ Add Widget" — try a KPI (pick a numeric column +
   `sum`), then a Chart (categorical X column, numeric Y column, `avg`).
7. Confirm you can drag a widget to reposition it and resize it from the
   bottom-right corner, and that the layout survives a page refresh
   (positions are persisted via `PATCH`).

## What Phase 7 Delivers

- **Two developer-experience fixes carried over from Phase 6 testing**,
  landed at the start of this phase rather than deferred further:
  - `/docs` now has a real global **"Authorize" padlock** —
    `get_current_user` switched from reading the `Authorization` header
    manually to FastAPI's `HTTPBearer` security scheme, which is what
    makes Swagger UI recognize it and paste the token into every request
    automatically instead of per-endpoint.
  - The dashboard page has an inline **"Create workspace"** form —
    creating an Organization + Workspace no longer requires going to
    `/docs` and calling two endpoints by hand.
- **SQL safety guard** (`app/services/sql_guard.py`) — the actual security
  boundary for AI-generated queries. Rejects anything that isn't a single
  SELECT/WITH statement, blocks a keyword list (INSERT/UPDATE/DELETE/DROP/
  ALTER/etc.), rejects statement chaining, and enforces a row limit.
  Keyword-based rather than a full parser — deliberately, see the module's
  docstring for why that's an acceptable tradeoff given where queries
  actually execute (next point).
- **Sandboxed execution** (`app/services/query_executor.py`) — validated
  SQL runs against an ephemeral **in-memory SQLite** database holding only
  that one dataset's rows, created fresh per request and discarded after.
  Never touches the app's own Postgres or a customer's connected database.
- **NL → SQL** (`app/services/nl_to_sql.py`) — Claude sees only column
  names, types, and a few sample values (never the full dataset), and
  returns `{sql, explanation}` as JSON. The `explanation` is what always
  gets shown to the user alongside the raw SQL — explainability was a
  goal from the very first project outline, not an afterthought.
- **Caching** — same immutable-version pattern as Phase 5: cache key is
  `(version_id, question)`, so a repeated question costs zero LLM tokens
  on a cache hit.
- **11 passing unit tests** covering the guard and executor — the two
  parts that don't need a live Anthropic API key. `nl_to_sql.py`'s Claude
  call itself needs manual testing (see below).
- **Frontend**: a `/datasets/[id]/query` page — ask in plain English, see
  the generated SQL, the explanation, and a results table.

## What Phase 7 Deliberately Does NOT Include

- **Querying DB-connector datasets** — same file-upload-only limitation
  carried from every data-reading endpoint since Phase 4. Generating and
  running arbitrary SQL against a customer's actual connected database is
  meaningfully riskier and is not in scope here.
- **Multi-turn conversation / follow-up questions** — each question is
  independent; conversational context ("now break that down by region")
  is a natural Phase 8 (AI Insights) extension, not this phase.
- **A full SQL parser for validation** — see `sql_guard.py`'s docstring;
  the keyword-blocklist approach is intentional given the sandbox it
  backs, not a shortcut that needs revisiting immediately.

## Testing Phase 7

1. `pip install -r requirements.txt` again (adds `anthropic`).
2. Add `ANTHROPIC_API_KEY` to `.env` (get one at console.anthropic.com if
   you don't have one — this phase genuinely needs a live key, there's no
   mock mode).
3. No new migration — this phase doesn't touch Postgres at all.
4. Optional: `cd apps/api && python tests/test_ai_query.py` — runs the 11
   unit tests standalone, no API key needed for these.
5. Restart `uvicorn` and `npm run dev`. On `/docs`, click **"Authorize"**
   once (top-right) and paste `Bearer <token>` — confirm it now applies to
   every endpoint without re-entering it.
6. On `/dashboard`, use the new inline form to create a workspace directly
   (no more manual `/docs` calls needed for this).
7. Go to a dataset, click "Ask →", try a question like "what's the average
   \<numeric column\> by \<categorical column\>?" — confirm you see the
   generated SQL, a plain-language explanation, and a results table.
8. Ask the same question again — confirm the response comes back
   instantly and shows `cached: true`.
9. Try something like "delete all rows" to confirm the guard blocks it
   with a 400 rather than the query ever reaching execution.

## A Bug Fixed From Phase 7

While building this phase's `ai_narration.py` alongside Phase 7's
`nl_to_sql.py`, a real bug surfaced: `nl_to_sql.py` called
`anthropic.Anthropic(...)` but never imported the `anthropic` module. It
went unnoticed in Phase 7 because that code path only runs with a live
`ANTHROPIC_API_KEY` set, which wasn't exercised yet. Fixed here (now a lazy
`import anthropic` inside the function, matching this phase's pattern) —
called out explicitly rather than silently folded in, since "phases build
on each other" cuts both ways: a latent bug from an earlier phase is worth
surfacing, not just quietly patching.

## What Phase 8 Delivers

- **Statistics decide, AI only phrases** — `app/services/insights.py`
  detects and ranks candidate insights (outliers via z-score, correlations
  via Pearson, categorical imbalance, linear trends over time) using pure
  pandas math. `app/services/ai_narration.py` then asks Claude to phrase
  *only the pre-computed numbers* as a sentence — the system prompt
  explicitly forbids estimating or inventing any number not given to it.
  This is the same "AI narrates, doesn't decide" split promised back in
  the original roadmap for this phase.
- **Graceful AI degradation** — if the Claude call fails (no API key,
  network issue, bad response), `fallback_narration()` produces a
  template-based sentence per insight instead of failing the request.
  Every insight always has *some* readable description; the AI narration
  layer can go down without the feature going down.
- **12 passing unit tests** — including one that specifically verifies
  insights come back sorted by significance, and one exercising every
  fallback-narration template.
- **Cached by version_id** — same pattern as Phase 5 (EDA) and Phase 7
  (query), since DatasetVersions are immutable.
- **Frontend**: a `/datasets/[id]/insights` page — each insight shown with
  its type, significance score, and plain-language narration; a visible
  indicator when narration fell back to templates (so the degradation is
  honest, not silently hidden from the user).

## What Phase 8 Deliberately Does NOT Include

- **Insights on DB-connector datasets** — same file-upload-only limitation
  as every data-reading endpoint since Phase 4.
- **Anomaly detection across dataset versions** ("this changed since last
  week") — this phase profiles a single version in isolation. Comparing
  versions over time is a natural Phase 13 (Version History) extension.
- **User-configurable significance thresholds** — the thresholds in
  `insights.py` (z-score > 2, |correlation| > 0.5, dominant category >
  60%, trend r² > 0.3) are fixed constants for now, not per-workspace
  settings.

## Testing Phase 8

1. No new Python dependencies — `anthropic` has been in requirements.txt
   since Phase 7.
2. No new migration — this phase only reads data and writes to Redis
   cache, same as Phase 5 and 7.
3. Optional: `cd apps/api && python tests/test_insights.py` — runs the 12
   unit tests standalone, no API key needed.
4. Restart `uvicorn`, go to a dataset, click "Insights →".
5. With `ANTHROPIC_API_KEY` set: confirm each insight has a natural,
   readable sentence and `ai_narration_used` shows true (no fallback
   banner).
6. Without a key (or temporarily blank it in `.env` and restart): confirm
   insights still appear, with the amber "plain-text descriptions" notice
   — this is the fallback path working as intended, not a bug.
7. Click "Recompute" to confirm the cache-bypass path also works.

## What Phase 9 Delivers

- **One-click dashboard generation** — `POST .../generate-dashboard`
  reuses Phase 5's EDA functions to build a candidate widget list, has
  Claude pick + title the best 4-6 of them, packs a grid layout
  deterministically, and saves it using Phase 6's exact
  Dashboard/DashboardWidget models. A generated dashboard is a normal
  dashboard afterward — editable, deletable, same as one built by hand.
- **AI never invents a column reference** — the model only selects
  indices from a pre-validated candidate list (`build_candidates`); an
  out-of-range or malformed index from the model is silently skipped
  rather than trusted, so a hallucinated response degrades the widget
  count, not the request's correctness.
- **Graceful fallback** — `select_fallback` picks a sensible default set
  (2 KPIs, 3 charts, 1 table) with zero AI involvement when the Claude
  call fails, same degradation pattern as Phase 8.
- **13 passing unit tests** covering candidate building, fallback
  selection, layout packing, and — importantly — response validation
  including a hallucinated out-of-range index.
- **Frontend**: a "✨ Generate Dashboard" button on each dataset that
  creates the dashboard and redirects straight into Phase 6's canvas to
  view/edit it.

## What Phase 9 Deliberately Does NOT Include

- **Generating from DB-connector datasets** — same file-upload-only
  limitation carried from every data-reading phase since Phase 4.
- **Grid layout decided by AI** — intentionally deterministic
  (`pack_layout`), not a prompt — see the service module's docstring.
- **Regenerating/updating an existing generated dashboard** — each
  generation creates a new dashboard; refining one currently means editing
  it by hand in Phase 6's canvas.

## Testing Phase 9

1. No new Python dependencies — `anthropic` has been in requirements.txt
   since Phase 7.
2. No new migration — this phase only creates ordinary
   Dashboard/DashboardWidget rows via Phase 6's existing tables.
3. Optional: `cd apps/api && python tests/test_dashboard_generator.py` —
   runs the 13 unit tests standalone, no API key needed.
4. Restart `uvicorn` and `npm run dev`, go to `/datasets`, click
   "✨ Generate Dashboard" on any ready file-upload dataset.
5. Confirm you land on a new dashboard with a handful of widgets already
   placed and titled, and that dragging/resizing them (Phase 6) still
   works normally.
6. Try it without `ANTHROPIC_API_KEY` set (temporarily blank it and
   restart) — confirm a dashboard still gets created via the fallback
   path rather than the request failing.

## What Phase 10 Delivers

- **AutoML (`app/services/automl.py`)** — pick any column as a target and
  the service auto-detects regression vs. classification from its dtype
  and cardinality (a numeric column with only a handful of distinct
  values, e.g. 0/1 flags, is treated as classification, not a continuous
  target). A shared preprocessing pipeline (median-impute + scale numeric,
  most-frequent-impute + one-hot categorical) feeds two candidate
  estimators per task (linear/logistic regression and a random forest);
  both are evaluated on a held-out split and the better one — by R² for
  regression, macro F1 for classification — is refit on the full dataset
  and shipped. Near-unique text columns (order IDs, free-text names) are
  auto-excluded from features as identifier noise.
- **Feature importance, honestly attributed** — one-hot-expanded columns
  are summed back to their original column name (via the exact
  `ColumnTransformer` segment widths, not string-parsing), then normalized
  to sum to 1, so "which columns mattered" is reported per real column,
  not per dummy variable.
- **Forecasting (`app/services/forecasting.py`)** — pick a date column and
  a numeric value column; the service resamples to daily/weekly/monthly
  (inferred from the median gap between timestamps, or pinned explicitly),
  fits a linear trend plus seasonal dummy variables via OLS once there's
  enough history for two full seasonal cycles, backtests against the tail
  of the series, then refits on everything and projects `horizon` periods
  forward with 80%/95% prediction intervals that widen with the square
  root of the step count.
- **"AI narrates, code validates" — same split as Phases 7-9** —
  `automl.py` and `forecasting.py` never call Claude. The only AI touch
  point is `ai_narration.py`'s new `narrate_model_result()` /
  `narrate_forecast()`, each handed the exact metrics already computed and
  asked only to phrase them in plain language; both have a template
  fallback (`fallback_model_narration()` / `fallback_forecast_narration()`)
  used automatically when the Claude call fails or no API key is set —
  same degradation guarantee as insights (Phase 8) and dashboard
  generation (Phase 9).
- **Persisted, immutable runs** — `MLModel` and `Forecast` rows are tied to
  one specific `dataset_version_id` and never mutated in place; a re-run
  (e.g. against a newer version) creates a new row, mirroring
  `DatasetVersion`'s own lineage posture. A trained model's serialized
  `sklearn` `Pipeline` is joblib-dumped into object storage the same way
  raw files are stored, keyed under
  `workspaces/{id}/datasets/{id}/models/{model_id}/pipeline.joblib`;
  everything else (metrics, feature importance, the candidate leaderboard,
  forecast points) is small enough to live inline as JSONB.
- **API**:
  - `POST/GET/DELETE .../versions/{vid}/models[/{model_id}]` — train, list,
    get (with narration), delete
  - `POST .../models/{model_id}/predict` — run the stored pipeline on new
    rows; missing feature keys are imputed the same way training-time nulls
    were, not rejected
  - `POST/GET/DELETE .../versions/{vid}/forecasts[/{forecast_id}]` — run,
    list, get (with narration), delete
- **30 passing unit tests** (16 AutoML + 14 forecasting) covering task-type
  detection, feature selection (including identifier exclusion and
  explicit overrides), training + metrics for both task types, the
  serialize/deserialize round-trip, frequency inference, seasonality
  detection, backtest behavior, and input-validation error paths.

## What Phase 10 Deliberately Does NOT Include

- **DB-connector datasets** — same file-upload-only limitation carried
  from every data-processing phase since Phase 4.
- **Deep learning / gradient boosting / statsmodels-based models** —
  candidates are intentionally limited to linear/logistic regression and
  random forest; this environment doesn't ship `statsmodels`, so
  forecasting uses a transparent OLS trend+seasonality fit rather than
  ARIMA/Prophet-style models. Swapping in a heavier candidate set later
  is additive, not a breaking change to the API shape.
- **Hyperparameter tuning** — each candidate estimator trains with fixed,
  reasonable defaults; no grid/random search.
- **Model retraining/versioning UI, or scheduled/recurring forecasts** — a
  model or forecast is a one-shot run today; re-running against a newer
  dataset version is a new POST, not an update.
- **Background job queue** — training/forecasting both run synchronously
  within the request (Redis is still unused as a queue, only as EDA/
  query/insight response cache from earlier phases); large datasets or
  slow training would block the request in this phase.
- **Frontend UI for Phase 10** — this phase is API + service-layer only,
  same as most phases; a "Train Model" / "Forecast" panel in the Next.js
  app is not part of this delivery.

## Testing Phase 10

1. New Python dependencies — `scikit-learn==1.5.2` and `joblib==1.4.2`
   added to `apps/api/requirements.txt`. Run `pip install -r
   requirements.txt` (or rebuild the API container) before starting the
   server.
2. New migration — run `alembic upgrade head` to create `ml_models` and
   `forecasts` (revision `0005`, chained after Phase 6's `0004`).
3. `cd apps/api && python tests/test_automl.py` — 16 unit tests, pure
   pandas/sklearn synthetic data, no DB/API key needed.
4. `cd apps/api && python tests/test_forecasting.py` — 14 unit tests, pure
   pandas/numpy/sklearn synthetic data, no DB/API key needed.
5. Manual check, AutoML: `POST
   /workspaces/{id}/datasets/{did}/versions/{vid}/models` with a
   `target_column` from a ready file-upload dataset. Confirm the response
   has `task_type`, a winning `algorithm`, `metrics`, ranked
   `feature_importance` summing to ~1.0, and a `narration` object.
6. Manual check, prediction: `POST .../models/{model_id}/predict` with a
   `rows` array (plain dicts); confirm predictions come back and that
   omitting a feature key doesn't error (it's imputed).
7. Manual check, forecasting: `POST
   /workspaces/{id}/datasets/{did}/versions/{vid}/forecasts` with a
   `date_column` and `value_column` from a dataset with enough history
   (8+ periods). Confirm `forecast` has `horizon` points with widening
   `lower_95`/`upper_95` bounds further out, and `metrics` is populated
   when there's enough history to backtest.
8. Without `ANTHROPIC_API_KEY` set (temporarily blank it and restart):
   confirm both endpoints still return a `narration` (via the template
   fallback) with `ai_narration_used: false`, rather than failing.

## What Phase 11 Delivers

- **PDF and PPTX export of any Dashboard** — `POST
  /workspaces/{id}/dashboards/{did}/reports` walks every widget on a
  dashboard (chart, table, KPI, text), computes its render data with the
  *exact same* code Phase 6's "render this widget" endpoint uses
  (`app/services/aggregation.py`'s `compute_chart`/`compute_table`/
  `compute_kpi`, against `dataset.versions[0]` as the current version), and
  lays the results out as a downloadable file. A report always shows what
  the dashboard would show if opened right now — nothing is re-derived or
  approximated for the export.
- **One rendering path for both formats
  (`app/services/report_generator.py`)** — chart widgets are drawn once
  with `matplotlib` (Agg backend) to a PNG and embedded as an image in
  both the PDF and the PPTX, rather than using either library's native
  chart objects. This avoids the "PowerPoint says the file is corrupt"
  failure mode that native chart XML can produce, at the cost of the chart
  not being editable inside PowerPoint — a documented tradeoff, not an
  oversight.
- **AI executive summary, same split as every AI feature since Phase 8** —
  `report_generator.py` never calls Claude. A report's cover-page summary
  is written by `ai_narration.py`'s new `narrate_report_summary()`, handed
  a code-only payload (dashboard name, every KPI's exact value, each
  chart's top category and value) assembled by `app/api/reports.py`, and
  asked only to phrase those numbers in 2-4 sentences. `fallback_report_summary()`
  produces a template version from the same payload when the Claude call
  fails or no API key is set — a report is never generated without some
  cover summary.
- **Graceful per-widget degradation** — a widget backed by a DB-connector
  dataset, a missing dataset, or a bad config is skipped with no entry in
  the report rather than failing the whole export; `POST` only fails
  outright if *every* widget was unrenderable (nothing to put in the
  report at all).
- **Persisted, immutable reports** — same posture as `MLModel`/`Forecast`:
  a `Report` row is tied to the dashboard's state at generation time and
  never mutated; regenerating creates a new row. The file itself is
  joblib-adjacent in spirit to `MLModel`'s storage pattern — uploaded to
  object storage at
  `workspaces/{id}/dashboards/{id}/reports/{report_id}.{pdf|pptx}` — with
  small metadata (title, format, status, widget count, file size, the
  cached summary) inline in Postgres.
- **API**:
  - `POST/GET/DELETE .../dashboards/{did}/reports[/{report_id}]` —
    generate, list, get metadata, delete
  - `GET .../reports/{report_id}/download` — streams the file back with
    the correct `Content-Type` and `Content-Disposition` for the format
- **12 passing unit tests** covering PNG chart rendering (including empty
  data and the line-chart variant), PDF generation with every widget type
  (verified via `pypdf` — real page content, not just "some bytes came
  out"), PPTX generation with every widget type (verified via
  `python-pptx` — real slide count, table dimensions, widescreen slide
  size), table truncation notes, missing-summary handling, empty-dashboard
  rejection, and graceful handling of an unsupported widget type.

## What Phase 11 Deliberately Does NOT Include

- **Native, in-app-editable PowerPoint charts** — charts are flattened to
  images (see above); a viewer can't click a bar and edit its underlying
  data inside PowerPoint. Swapping in `python-pptx`'s native chart API
  later is additive, not a breaking change to the report's shape.
- **Custom report templates / branding upload** — layout, fonts, and the
  brand color are fixed in code today; no per-workspace template picker.
- **Scheduled/recurring report generation or email delivery** — a report
  is a one-shot, on-demand export; no cron-style "send me this every
  Monday" yet.
- **DB-connector datasets** — same file-upload-only limitation carried
  from every data-processing phase since Phase 4; a widget on a
  DB-connector dataset is silently skipped rather than erroring (see
  "graceful per-widget degradation" above).
- **Background job queue** — generation runs synchronously in the
  request, same as every phase so far; a dashboard with many
  chart-heavy widgets will hold the request open while `matplotlib`
  renders each one.
- **Frontend UI for Phase 11** — API + service-layer only; a "Export as
  PDF/PPTX" button in the Next.js app is not part of this delivery.

## Testing Phase 11

1. New Python dependencies — `reportlab==4.2.5`, `python-pptx==1.0.2`, and
   `matplotlib==3.9.2` added to `apps/api/requirements.txt`. Run `pip
   install -r requirements.txt` (or rebuild the API container) before
   starting the server.
2. New migration — run `alembic upgrade head` to create `reports`
   (revision `0006`, chained after Phase 10's `0005`).
3. `cd apps/api && python tests/test_report_generator.py` — 12 unit
   tests, pure synthetic widget data, no DB/API key needed.
4. Manual check, PDF: `POST
   /workspaces/{id}/dashboards/{did}/reports` with `{"format": "pdf"}`
   against a dashboard that has at least one chart, table, KPI, and text
   widget. Confirm the response has `status: "ready"`, a `summary`, and a
   nonzero `file_size_bytes`.
5. Manual check, download: `GET .../reports/{report_id}/download` and
   confirm the browser/client receives a valid PDF with a cover page
   (title + executive summary) followed by one section per widget, charts
   rendered as images.
6. Manual check, PPTX: repeat with `{"format": "pptx"}`; confirm a title
   slide, a summary slide, and one slide per widget, with the same 16:9
   layout in every slide.
7. Without `ANTHROPIC_API_KEY` set (temporarily blank it and restart):
   confirm a report still generates successfully with a template
   `summary` and `ai_narration_used: false` in the response, rather than
   failing.
8. Manual check, graceful degradation: point one widget at a
   DB-connector dataset (or delete its underlying dataset) and regenerate
   a report; confirm the report still generates successfully with that
   widget simply absent, and only fails outright if you do this to every
   widget on the dashboard.

## What Phase 12 Delivers

- **Workspace invites by email, including non-existing users** — the piece
  Phase 2's `workspaces.py` explicitly deferred. `POST
  /workspaces/{id}/invites` (ADMIN+) creates a `WorkspaceInvite` with a
  random 32-byte token (`secrets.token_urlsafe`) and a 7-day expiry,
  regardless of whether that email has ever signed in. `GET
  /invites/{token}` (any authenticated user) shows the invite's workspace
  name/role/status before accepting; `POST /invites/{token}/accept`
  becomes a `WorkspaceMember` only if the current user's email matches the
  invite's email — so accepting means "sign in with the invited address,"
  not "guess a token." `DELETE .../invites/{id}` revokes; `POST
  .../invites/{id}/resend` re-sends and refreshes the expiry.
- **Member role management, completing Phase 2's RBAC** — `PATCH
  /workspaces/{id}/members/{user_id}` (ADMIN+) changes a member's role;
  `DELETE .../members/{user_id}` removes one; `POST
  /workspaces/{id}/leave` lets you remove yourself. All three refuse to
  demote/remove/leave-as the workspace's *last* OWNER (`_owner_count()`
  guard), so a workspace can never end up with nobody able to manage it.
  Promoting someone *to* OWNER additionally requires the actor to already
  be an OWNER — an ADMIN can't hand out ownership.
- **Graceful email degradation (`app/services/email.py`)** — extends the
  "never hard-fail, only degrade" philosophy from Phase 8's AI fallbacks
  to a second kind of external dependency: if `SMTP_HOST` isn't configured,
  `send_invite_email()` logs the would-be email (including the accept URL)
  and returns `False` instead of raising, so invites work out of the box
  in dev with zero mail setup. When SMTP *is* configured, a real send
  failure raises `EmailSendError` rather than silently swallowing it —
  configured-but-broken is worth surfacing; not-configured is not.
- **Dashboard comments, threaded one level deep**
  (`app/models/dashboard_comment.py`, `app/api/comments.py`) — `POST
  /workspaces/{id}/dashboards/{did}/comments` adds a comment, optionally
  pinned to one `widget_id` and/or replying to one `parent_comment_id`.
  VIEWER+ can read and comment (feedback shouldn't require edit rights);
  editing/deleting requires being the author or ADMIN+; resolving/
  reopening a thread requires EDITOR+ regardless of authorship. Unlike
  DatasetVersion/MLModel/Report, comments are intentionally mutable in
  place — a live conversation, not a derived artifact.
- **Append-only workspace activity feed**
  (`app/models/activity_log.py`, `app/services/activity.py`,
  `app/api/activity.py`) — every invite/member/comment action in this
  phase writes one `ActivityLog` row via `log_activity()` in the *same*
  transaction as the mutation it describes (flushed, not committed, until
  the caller's own `db.commit()`), so a log entry never exists for a
  mutation that didn't actually happen. `GET /workspaces/{id}/activity`
  reads it back cursor-paginated on `created_at` (`before=<timestamp>`,
  `limit`, optional `action` filter) rather than offset-paginated, since
  an ever-growing append-only log gets worse with offset paging the
  further back you go.
- **Migration `0007`** — adds `workspace_invites`, `dashboard_comments`,
  `activity_logs`; reuses the existing `workspace_role` enum from `0001`
  for an invite's role rather than introducing a parallel one.
- **8 passing unit tests** covering invite expiry/pending logic (future,
  past, revoked-but-not-expired, and a defensively naive-datetime case),
  the activity action vocabulary, and the email service's no-SMTP
  degradation path.

## What Phase 12 Deliberately Does NOT Include

- **Real-time updates** — no websockets/SSE for live comment/activity
  feeds; clients poll `GET` endpoints. Live presence ("who's viewing this
  dashboard right now") is not built.
- **Notifications** — an invite triggers exactly one email attempt; there's
  no in-app notification center, no "you were mentioned" alert on
  comments, and no digest of activity.
- **Activity logging for pre-Phase-12 actions** — dataset uploads,
  dashboard edits, report generation, etc. don't write `ActivityLog` rows
  yet. `log_activity()` is generic enough for later phases to call from
  their own mutations, but retrofitting every earlier phase's routes is
  out of scope here (see "PHASE-GATED WORKFLOW RULES" — no half-built
  cross-phase dependencies).
- **Nested replies beyond one level** — a reply-to-a-reply is rejected
  (400) rather than silently threading deeper; the data model
  (`parent_comment_id`) could support it, but the API deliberately caps it.
- **Rich/HTML email** — `send_invite_email()` sends plaintext via stdlib
  `smtplib`; no templated HTML, no transactional-email provider SDK.
- **Frontend UI for Phase 12** — API + service-layer only, consistent with
  Phases 7-11; invite/member-management/comment/activity screens in the
  Next.js app are not part of this delivery.
- **Organization-level (cross-workspace) roles or teams** — invites and
  membership stay workspace-scoped, matching every RBAC decision since
  Phase 2.

## Testing Phase 12

1. New migration — run `alembic upgrade head` to create
   `workspace_invites`, `dashboard_comments`, `activity_logs` (revision
   `0007`, chained after Phase 11's `0006`). No new Python dependencies.
2. `cd apps/api && python tests/test_team_collaboration.py` — 8 unit
   tests, pure Python, no DB/network needed.
   > This sandbox had no outbound network access while building this
   > phase, so `fastapi`/`sqlalchemy`/etc. could not be installed here to
   > actually execute the suite (the same would be true of any earlier
   > phase's tests run fresh in this container — `pip install -r
   > requirements.txt` fails the same way). Every file was verified with
   > `python -m py_compile` and reviewed by hand against the same logic
   > the tests assert; please run the real suite after `pip install -r
   > requirements.txt` (or in the API container) as the first check.
3. Manual check, invite by non-existing email: `POST
   /workspaces/{id}/invites` with an email that's never signed in. Confirm
   a `WorkspaceInvite` is created (check the API logs for the "[dev-mode
   email]" line with the accept URL, since `SMTP_HOST` is blank by
   default). Sign in as that email via Clerk, then `POST
   /invites/{token}/accept` and confirm you're now a workspace member with
   the invited role.
4. Manual check, email mismatch: try accepting an invite while signed in
   as a *different* email than the invite — confirm `403`.
5. Manual check, expiry/revoke: manually set an invite's `expires_at` to
   the past (or `DELETE` it to revoke) and confirm `accept` returns `410`
   (expired) or `409` (already revoked) respectively.
6. Manual check, last-owner protection: as the sole OWNER, try `PATCH
   .../members/{yourself}` to `viewer`, `DELETE .../members/{yourself}`,
   and `POST .../leave` — confirm all three `409`. Promote a second member
   to OWNER first, then confirm the same actions succeed.
7. Manual check, comments: add a top-level comment and a reply on a
   dashboard as a VIEWER; confirm both appear via `GET .../comments` with
   the reply nested under `replies`. Confirm a second-level reply (replying
   to a reply) is rejected with `400`. Resolve the thread as an EDITOR and
   confirm `resolved: true` with your email in `resolved_by_email`.
8. Manual check, activity feed: after steps 3-7, `GET
   /workspaces/{id}/activity` and confirm entries appear for each action,
   newest first; try `?action=member.invite_accepted` and `?limit=2` to
   confirm filtering and pagination.

## What Phase 13 Delivers

- **Automatic dashboard version history** (`app/models/dashboard_version.py`,
  `app/services/dashboard_versioning.py`) — every shape-changing action on
  a dashboard (create, rename, widget add/update/delete) now also writes
  an immutable `DashboardVersion` snapshot in the *same* transaction as
  the change, via `create_version()` (flush-not-commit, same pattern as
  Phase 12's `log_activity`). Snapshots are never created directly by a
  client — the history is always complete, not opt-in per edit. This is
  the one major mutable-in-place resource that didn't already have
  version history: `DatasetVersion` has had it since Phase 3, and
  `MLModel`/`Forecast`/`Report` are immutable rows already.
- **Full widget-list snapshots, not deltas** — `serialize_widgets_for_snapshot()`
  freezes every widget's type, title, dataset reference, config, and grid
  position into a plain JSONB list on the version row, independent of the
  live `DashboardWidget` rows (which can keep changing or be deleted
  after the snapshot is taken).
- **Diffing between any two versions** — `diff_widget_snapshots()` (pure,
  no DB) compares two snapshot lists keyed on each widget's id and reports
  `added` / `removed` / `modified` (with a per-field before/after) /
  `unchanged_count`. Exposed as `GET
  .../versions/{from}/diff/{to}`.
- **Restore, without destroying history** — `POST
  .../versions/{n}/restore` (EDITOR+) replaces the live widget set with
  version `n`'s snapshot and renames the dashboard back to match, then
  immediately takes a *new* snapshot on top (change_summary "Restored from
  version n"). Nothing is deleted or rewritten — restoring to version 3
  when you're on version 8 produces version 9, so the full timeline
  including the restore itself stays intact, same "never mutate history,
  append instead" posture as everywhere else immutability shows up in
  this codebase.
- **Graceful handling of widgets whose dataset disappeared** — if a
  snapshot references a `dataset_id` that no longer exists in the
  workspace by the time of restore, the widget is still restored (title,
  config, position intact) with the dangling reference dropped rather
  than failing the whole restore; the response's
  `skipped_missing_datasets` count says how many.
- **API**:
  - `GET .../dashboards/{did}/versions` — list snapshots, newest first
    (summary: version number, name, widget count, change summary, who,
    when)
  - `GET .../versions/{n}` — one snapshot's full widget list
  - `GET .../versions/{from}/diff/{to}` — added/removed/modified between
    two snapshots
  - `POST .../versions/{n}/restore` — restore, returns the new version
    number and how many widgets were restored/skipped
  - `PATCH /workspaces/{id}/dashboards/{did}` — new: rename a dashboard
    (needed a version-worthy mutation to rename into; didn't exist before
    this phase)
- **Migration `0008`** — adds `dashboard_versions`, unique on
  `(dashboard_id, version_number)`.
- **8 passing unit tests** covering version-number allocation and every
  diff case (no changes, added, removed, single-field modified,
  multi-field modified, and the "restore looks like full replace since
  ids differ" case) — all pure-Python, no DB.

## What Phase 13 Deliberately Does NOT Include

- **Version history for anything other than dashboards** — `Dataset`
  already had it since Phase 3; comments, invites, activity logs, and
  workspace membership are not versioned (an activity-log entry already
  covers "what changed and who did it" for those — see Phase 12).
- **Named/labeled versions or manual "save as version" snapshots** —
  every snapshot is automatic and tied to a specific detected action;
  there's no "tag this as v1.0" or free-form manual checkpoint yet.
- **Diffing widget *data* (the rendered chart/table/KPI values)** — the
  diff compares widget *definitions* (config, position, title), not
  whether the underlying dataset's numbers changed between two points in
  time. A widget's rendered output can drift even with zero dashboard
  versions created, since it's computed live against the dataset's
  current version (Phase 6 behavior, unchanged).
- **Version pruning/retention limits** — history grows unbounded; no
  "keep last N versions" or archival policy.
- **Frontend UI for Phase 13** — API + service-layer only, consistent
  with Phases 7-12; a visual history timeline / diff viewer in the
  Next.js app is not part of this delivery.

## Testing Phase 13

1. New migration — run `alembic upgrade head` to create
   `dashboard_versions` (revision `0008`, chained after Phase 12's
   `0007`). No new Python dependencies.
2. `cd apps/api && python tests/test_dashboard_versioning.py` — 8 unit
   tests, pure Python, no DB/network needed.
   > Same sandbox limitation noted in Phase 12's testing section applies
   > here: no outbound network access while building this phase, so the
   > full dependency set (`fastapi`/`sqlalchemy`/etc.) couldn't be
   > installed to run this file as-is (it imports `app.services.
   > dashboard_versioning`, which imports SQLAlchemy transitively through
   > the model modules). Every file passed `python -m py_compile`, and —
   > beyond that — the diff/next-version-number logic in this test file
   > was additionally copy-verified by extracting the two pure functions
   > into a dependency-free scratch module and actually running all 8
   > assertions against it (all passed); the delivered test still imports
   > the real module, as every other phase's tests do, so please run it
   > for real after `pip install -r requirements.txt`.
3. Manual check, snapshots are automatic: create a dashboard, add two
   widgets, edit one, delete the other, then `GET .../versions` — confirm
   5 versions exist (create, add ×2, update, delete) with sensible
   `change_summary` text on each, oldest as version 1.
4. Manual check, snapshot detail: `GET .../versions/1` and confirm
   `widgets: []` (dashboard had none yet); `GET .../versions/2` and
   confirm the first widget appears with its title/config/position.
5. Manual check, diff: `GET .../versions/1/diff/5` and confirm `added`
   lists whatever widget(s) survived to version 5, `removed` lists the
   one that was deleted along the way, and `modified` reflects the edit
   (with a `changes` breakdown per field).
6. Manual check, restore: `POST .../versions/2/restore`; confirm the
   dashboard's live widgets now match version 2 (i.e. the widget that was
   later deleted is back, with a new id), the response's
   `new_version_number` is 6, and `GET .../versions` now shows 6 entries
   with version 6's `change_summary` reading "Restored from version 2".
7. Manual check, dangling dataset on restore: delete the dataset a
   version-2 widget pointed at, then repeat the restore in step 6; confirm
   it still succeeds, the widget comes back with `dataset_id: null`, and
   `skipped_missing_datasets: 1` in the response.
8. Manual check, rename: `PATCH /workspaces/{id}/dashboards/{did}` with a
   new `name`; confirm a new version is created with a change_summary like
   `Renamed from "X" to "Y"`.

## What Phase 14 Delivers

- **Organization is the billing entity, as scaffolded since Phase 1** —
  `stripe_customer_id` and `plan` already existed on `Organization`; this
  phase adds `stripe_subscription_id`, `subscription_status`,
  `current_period_end`, `cancel_at_period_end` (migration `0009`) and
  makes them mean something. `plan` stays a plain string ("free" | "pro" |
  "enterprise"), not a DB enum — `app/services/plan_limits.py`'s
  `PLAN_LIMITS` dict is the single source of truth for what each one
  allows, same "vocabulary in code, not a migration" reasoning as
  Phase 12's `ActivityLog.action`.
- **Plan limits, enforced where usage actually grows**
  (`app/services/plan_limits.py`) — `max_workspaces` (org-wide) is
  checked in `create_workspace`; `max_seats_per_workspace` (counts active
  members *and* still-pending, non-expired invites, so you can't
  over-invite past the cap) is checked in both `add_member` and
  `invites.create_invite`. Exceeding either returns `402 Payment
  Required` with a message naming the current plan and its limit. Free:
  1 workspace / 3 seats each. Pro: 10 workspaces / 25 seats each.
  Enterprise: unlimited (`None` in `PLAN_LIMITS`).
- **Billing does NOT gracefully degrade the way AI/email do — and that's
  intentional** (`app/services/billing.py`'s module docstring spells out
  why). Reading plan/usage/limits (`GET .../billing`) never touches
  Stripe and always works off local data. Checkout and the billing portal
  *require* `STRIPE_SECRET_KEY`; without it they raise
  `BillingNotConfiguredError`, surfaced as `503`. There's no fake checkout
  session — you can't honestly fall back on "the customer paid."
- **Stripe Checkout + billing portal** — `POST
  .../billing/checkout-session` (`{"plan": "pro"|"enterprise", ...}`)
  creates/reuses the org's Stripe customer and returns a Checkout Session
  URL; `POST .../billing/portal-session` returns a Stripe-hosted portal
  URL for the org to manage or cancel its own subscription (requires a
  Stripe customer to already exist).
- **Webhook keeps `Organization` in sync**
  (`POST /billing/webhook`, `app/models/billing_event.py`) — signature-
  verified via the `stripe` SDK's `Webhook.construct_event`, the exact
  same "verify with the provider's SDK, don't trust the payload outright"
  pattern as Phase 2's Clerk webhook (svix). Handles
  `customer.subscription.created/updated` (updates plan from the Price id
  via `plan_for_price_id`, status, period end, cancel-at-period-end) and
  `customer.subscription.deleted` (reverts to `free`). Every event is
  recorded in `BillingEvent` keyed on Stripe's own event id, so a
  redelivered webhook (Stripe retries on timeout) is detected and skipped
  rather than double-applied — idempotency, not just an audit log.
- **Billing access without an organization-membership table** — Phase 12
  onward this codebase has deliberately had no `OrganizationMember`;
  access is derived from workspace roles. The new
  `require_organization_owner()` dependency (`app/api/deps.py`) follows
  that same design: anyone who is `OWNER` of at least one workspace under
  the org can view/manage its billing.
- **7 passing unit tests** covering plan-limit lookup/fallback (including
  the "unknown plan falls back to the *restrictive* free tier, not
  unlimited" case), the under-limit check for both finite and unlimited
  plans, and `plan_for_price_id`'s mapping (including a blank-config
  collision guard).

## What Phase 14 Deliberately Does NOT Include

- **Usage-based/metered billing** — plans are flat-rate seat/workspace
  caps, not pay-per-dataset or pay-per-AI-call. Anthropic API usage
  (Phase 7+) isn't metered back to the org's bill.
- **Proration, trials, coupons, invoices** — all handled Stripe-side via
  Checkout/the billing portal; this phase doesn't build custom UI or
  logic for any of them.
- **Enforcing `max_seats_per_workspace` retroactively** — if an org is
  downgraded (or a subscription lapses) while already over a lower plan's
  seat/workspace count, existing members/workspaces are not auto-removed.
  The cap only blocks *new* workspaces/invites going forward. Deciding
  what "over the limit after a downgrade" should do (grace period? hard
  block on new datasets? something else?) is a product decision left
  for a later phase, not silently guessed at here.
- **A dedicated OrganizationMember/organization-level role table** — see
  "What Phase 14 Delivers" above; billing access is still derived from
  workspace `OWNER` role, consistent with every phase since Phase 12.
- **Frontend UI for Phase 14** — API + service-layer only, consistent
  with Phases 7-13; a pricing/upgrade page, usage dashboard, or embedded
  Stripe Elements form in the Next.js app is not part of this delivery.

## Testing Phase 14

1. New migration — run `alembic upgrade head` to add the subscription
   columns to `organizations` and create `billing_events` (revision
   `0009`, chained after Phase 13's `0008`).
2. New dependency — `pip install -r requirements.txt` now also installs
   `stripe==11.1.1`.
3. `cd apps/api && python tests/test_billing.py` — 7 unit tests, pure
   Python, no DB/network/Stripe needed.
   > Same sandbox limitation noted in Phases 12 and 13's testing sections:
   > no outbound network access while building this phase, so `stripe`/
   > `fastapi`/`sqlalchemy` couldn't be installed here to run this file
   > as-is. Every file passed `python -m py_compile`, and the plan-limit
   > and `plan_for_price_id` logic was additionally copy-verified by
   > extracting it into a dependency-free scratch module and actually
   > running all 7 equivalent assertions against it (all passed). Please
   > run the real suite after `pip install -r requirements.txt`.
4. Manual check, limits without Stripe configured at all: with
   `STRIPE_SECRET_KEY` blank, `GET /organizations/{id}/billing` still
   works (`billing_configured: false`, plan `"free"`, limits/usage
   populated from local data). Try creating a 2nd workspace under a free
   org → `402`. Try inviting a 4th person into a workspace already at 3
   members → `402`.
5. Manual check, seat cap counts pending invites too: with a workspace at
   2/3 seats, send one invite (now "3 in use"), confirm a 2nd invite
   attempt also `402`s even though only 2 are actual members.
6. Manual check, checkout requires Stripe: with `STRIPE_SECRET_KEY` blank,
   `POST .../billing/checkout-session` → `503`. Set a real (or Stripe
   test-mode) secret key and price ids, retry → get back a real
   `checkout_url`.
7. Manual check, webhook end-to-end: using the Stripe CLI
   (`stripe listen --forward-to localhost:8000/billing/webhook`),
   complete a test-mode checkout and confirm `Organization.plan` flips to
   `"pro"`/`"enterprise"`, `subscription_status` becomes `"active"`, and a
   `BillingEvent` row exists for the event. Replay the same event
   (`stripe events resend <id>`) and confirm it's accepted but not
   double-applied (no duplicate `BillingEvent`, no re-run side effects).
8. Manual check, cancellation: cancel the test subscription via the
   billing portal (`POST .../billing/portal-session`) or the Stripe
   dashboard; confirm the `customer.subscription.deleted` webhook reverts
   the org to `plan: "free"`.

## What Phase 15 Delivers

- **Row-Level Security actually enforced, not just scaffolded** — this
  closes the loop Phase 1's initial migration deliberately left open (see
  that migration's own docstring: "RLS is enabled but the app currently
  connects as the table owner (bypasses RLS)... from Phase 15 onward...").
  Migration `0010`:
  - Adds `FORCE ROW LEVEL SECURITY` to `workspaces` (0001 only did
    `ENABLE`, which does nothing against an owner connection) and rolls
    out the identical `ENABLE + FORCE + policy` pattern to every other
    table with a direct `workspace_id` column: `activity_logs`,
    `dashboards`, `dashboard_comments`, `dashboard_versions`,
    `data_connections`, `datasets`, `forecasts`, `ml_models`, `reports`,
    `workspace_invites`, `workspace_members` — 12 tables total.
  - Creates a low-privilege `app_runtime` role (`NOSUPERUSER`,
    `NOBYPASSRLS`) with grants on every table plus
    `ALTER DEFAULT PRIVILEGES` so tables any *future* migration creates
    are automatically covered too. No password is set in the migration —
    that's an operator step (`ALTER ROLE app_runtime WITH PASSWORD ...`),
    since a committed migration is not where a secret belongs.
  - **Important, and stated plainly rather than glossed over**: in the
    default local docker-compose setup, `POSTGRES_USER` is created as a
    Postgres *superuser* by the official image's initdb — and superusers
    bypass RLS unconditionally, `FORCE` or not. `app_runtime` is
    deliberately not a superuser, which is what actually makes it
    subject to the policies. `FORCE` alone would matter for a
    non-superuser owner; here it's necessary but not sufficient without
    also connecting as `app_runtime`.
- **`app/core/rls.py`** sets `app.current_workspace_id` (the session GUC
  every policy reads) once per request via Postgres's `set_config(...,
  true)` function — a genuine bound parameter, never a value
  string-interpolated into `SET LOCAL ... = '<value>'` — and
  transaction-scoped (`is_local=true`) so it can never leak into whichever
  request reuses the same pooled connection next. Wired into
  `require_workspace_role` (`app/api/deps.py`) so it happens automatically
  for essentially every workspace-scoped route, right after the existing
  app-layer membership check — RLS is a *second* line of defense behind
  that check, not a replacement for it.
- **`app_db_url` / `effective_app_database_url`** (`app/core/config.py`,
  `app/core/database.py`) — the running API now connects with a
  separate, lower-privilege URL than Alembic's migration URL, falling
  back to the migration URL if unset so existing setups keep working
  unchanged (RLS policies exist either way; they're just not enforced
  against an owner connection until this is actually pointed at
  `app_runtime`).
- **Rate limiting** (`rate_limit()` in `app/api/deps.py`) — Redis-backed
  fixed-window counter, per user. Applied to the three endpoints where
  abuse has a real cost: AI query (20/min — Claude API calls), invite
  creation (30/hour — email-bombing prevention), Stripe checkout session
  creation (10/hour). Fails **open** if Redis is unreachable, same
  philosophy as Phase 5's cache and Phase 8/12's AI/email fallbacks — a
  non-critical-path dependency going down shouldn't take the API down
  with it, and rate limiting is abuse/cost protection layered on top of
  the real security boundary (auth + RBAC + RLS), not that boundary
  itself.
- **Security response headers** (`main.py` middleware) — on every
  response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: strict-origin-when-cross-origin`,
  `Permissions-Policy` (geolocation/camera/microphone all denied), and
  `Strict-Transport-Security` in production only (sending it in dev, over
  plain HTTP, does nothing useful and can make a browser keep demanding
  HTTPS on `localhost` afterward). Deliberately **no**
  Content-Security-Policy — FastAPI's built-in `/docs` loads its JS/CSS
  from `cdn.jsdelivr.net`, and a CSP strict enough to be worth adding
  would break it; a real CSP belongs on the Next.js frontend's actual HTML
  pages, not this JSON API.
- **A real CORS config fix, not just a Phase 15 add-on** — production
  CORS was previously hardcoded to `allow_origins=[]` with no environment
  variable to change it, meaning a production deployment's own frontend
  could never call the API from a browser at all. `CORS_ALLOWED_ORIGINS`
  (comma-separated) now populates it; still fails closed (empty) if
  unset in production — the safe default for a misconfigured
  deployment — but now there's an actual way to fix it. Dev always
  additionally allows `http://localhost:3000` regardless of this setting.
- **8 passing unit tests** covering the rate limiter's fixed-window
  bucketing (grouping, boundary-crossing, monotonicity),
  `set_workspace_context`'s SQL shape (uses `set_config` with a bound
  parameter, never interpolates the workspace id into the statement
  text), and the CORS origins fallback logic (dev always includes
  localhost, production fails closed when unconfigured, production uses
  only the configured list).

## What Phase 15 Deliberately Does NOT Include

- **RLS on `dashboard_widgets` or `dataset_versions`** — neither carries
  a direct `workspace_id` column (only `dashboard_id` / `dataset_id`),
  and their parent tables (`dashboards`, `datasets`) are already
  RLS-protected. A subquery-based policy for them is a reasonable future
  refinement, deliberately left out here rather than rushed — see
  migration `0010`'s docstring.
- **Automatically rotating or generating the `app_runtime` password** —
  intentionally an operator/ops-secrets-management step, not something a
  committed migration should do.
- **Auth-endpoint rate limiting** — Clerk owns login/session issuance
  (Phase 2); this codebase's own endpoints don't include a login route to
  rate-limit. The three limits added here are for this API's own
  cost/abuse surface.
- **A Web Application Firewall, DDoS protection, or IP-based blocking** —
  infrastructure/edge concerns (e.g. a CDN or reverse proxy), out of
  scope for the application code in this repo.
- **Encryption at rest beyond what already existed** — `DataConnection`
  credentials were already Fernet-encrypted (`app/core/crypto.py`,
  earlier phase); this phase doesn't add new field-level encryption
  elsewhere.
- **Dependency vulnerability scanning / SAST tooling** — a CI/pipeline
  concern (Phase 18: Testing, or ops tooling), not application code.
- **Frontend security work** — a real Content-Security-Policy, XSS
  hardening, etc. for the Next.js app's actual HTML pages is out of
  scope for this API-only phase.

## Testing Phase 15

1. New migration — run `alembic upgrade head` (as the admin/owner role —
   `DATABASE_URL`, unchanged) to apply RLS to all 12 tables and create
   `app_runtime` (revision `0010`, chained after Phase 14's `0009`). No
   new Python dependencies.
2. `cd apps/api && python tests/test_security.py` — 8 unit tests, pure
   Python, no DB/Redis/network needed.
   > Same sandbox limitation noted in Phases 12-14's testing sections: no
   > outbound network access while building this phase, so the full
   > dependency set couldn't be installed here to run this file as-is.
   > Every file passed `python -m py_compile`, and — importantly — the
   > pure logic itself (fixed-window bucketing, `set_workspace_context`'s
   > SQL shape, CORS fallback) was additionally copy-verified by
   > extracting it into a dependency-free scratch script and actually
   > running all 8 equivalent assertions (all passed). That process
   > caught a real bug in the first draft of the window-boundary test
   > (arbitrary epoch offsets don't line up with 60-second boundaries —
   > fixed by using clean offsets from 0) before it ever shipped. Please
   > still run the real suite after `pip install -r requirements.txt`.
3. Manual check, RLS actually enforced: set an `app_runtime` password
   (`ALTER ROLE app_runtime WITH PASSWORD '...'`), point `APP_DB_URL` at
   it, restart the API. As two different workspace owners in two
   different workspaces, confirm each can only ever see their own
   workspace's data via the normal API (expected — app-layer filtering
   already guaranteed this). Then, as a stronger check of the *DB* layer
   specifically: connect directly with `psql` as `app_runtime` and
   `SET app.current_workspace_id = '<workspace A's id>';` followed by
   `SELECT * FROM datasets;` — confirm only workspace A's datasets come
   back, even with no `WHERE` clause. Try `SELECT * FROM datasets;` with
   no `SET` at all — confirm *all* datasets come back (the deliberate
   `OR current_setting(...) IS NULL` escape hatch), then confirm that
   same unscoped query returns *all* datasets again even when connected
   as the plain admin role (`DATABASE_URL`) — showing FORCE + a
   non-superuser role is what made the difference, not the policy alone.
4. Manual check, docker-compose superuser gotcha: without switching
   `APP_DB_URL`, confirm the app still works exactly as before (RLS
   present but not enforced against the admin connection) — this is the
   documented, safe fallback state, not a broken one.
5. Manual check, rate limiting: call the AI query endpoint 21 times
   within 60 seconds as the same user — confirm the 21st returns `429`.
   Stop your local Redis and repeat — confirm requests succeed
   (fail-open) rather than every request erroring.
6. Manual check, CORS: with `ENVIRONMENT=production` and
   `CORS_ALLOWED_ORIGINS` unset, confirm a browser request from any
   origin is rejected by CORS. Set `CORS_ALLOWED_ORIGINS=https://your-
   frontend.example.com` and confirm requests from that origin succeed.
7. Manual check, headers: `curl -I` any endpoint and confirm
   `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and
   `Permissions-Policy` are present; confirm `Strict-Transport-Security`
   is present only when `ENVIRONMENT=production`.

## What Phase 16 Delivers

- **Structured JSON logging** (`app/core/logging_config.py`) — every log
  line is one JSON object (timestamp, level, logger, message, plus any
  `extra={...}` fields the caller passed), so log aggregation tooling
  (CloudWatch, Datadog, a self-hosted ELK stack) can filter/query on
  fields instead of regexing a formatted string. `LOG_LEVEL` (default
  `INFO`) controls verbosity; `uvicorn.access`/`httpx` are quieted to
  `WARNING` so normal operation isn't dominated by their own chatter.
- **Request correlation IDs** (`app/core/request_context.py`,
  `main.py`'s `request_context_middleware`) — every request gets an id
  (reusing an incoming `X-Request-ID` header if the client sent one, so a
  frontend-generated id survives end to end), echoed back in the
  response, and attached to *every* log line written during that
  request — including ones written deep inside a service function with
  no idea an HTTP request is involved — via a `contextvars`-based
  context, not a parameter threaded through every function signature.
  One structured `request_completed` (or `request_failed`) log line is
  also written per request automatically, so request-level observability
  doesn't depend on every route remembering to log anything itself.
- **`app/core/request_context.py` and `logging_config.py`'s
  `JSONFormatter` are deliberately dependency-free** (stdlib
  `contextvars`/`logging`/`json` only — `app.core.config` is imported
  lazily, inside `configure_logging()`'s body, not at module load). This
  wasn't just a nice-to-have: it's what let this phase's unit tests
  actually *run* in this sandbox, unlike every previous phase's — see
  "Testing Phase 16" below.
- **Sentry error tracking** (`app/core/error_tracking.py`) — same
  graceful-degradation posture as every other optional external
  dependency since Phase 8: no `SENTRY_DSN`, no-op, unhandled exceptions
  still propagate and get logged (just not also reported to Sentry). Even
  `sentry_sdk.init()` itself is wrapped in try/except — a bad DSN or
  incompatible SDK version at startup must never prevent the API from
  starting. `send_default_pii=False` — request bodies can contain
  dataset content or free-text AI questions, which shouldn't ride along
  in error reports by default.
- **Centralized Claude API client with Langfuse tracing**
  (`app/services/ai_client.py`) — before this phase, six call sites
  across `nl_to_sql.py`, `ai_narration.py` (×4), and
  `dashboard_generator.py` each independently built an
  `anthropic.Anthropic` client and duplicated the same response-text-
  extraction line. All six now call one `complete(feature=..., system=...,
  messages=..., max_tokens=...)` function, which adds Langfuse tracing
  (prompt, completion, token usage, latency) for every one of them, for
  free, if `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set — same
  fail-open posture as Sentry: every Langfuse-specific call is wrapped so
  a tracing failure can never break the actual AI feature. Each caller
  still owns its own domain-specific error type/message
  (`NLToSQLError`, `NarrationError`, `DashboardGeneratorError`) via its
  own `ANTHROPIC_API_KEY` check before calling `complete()`.
- **Richer health checks** (`app/api/health.py`) — `/health` stays a pure
  liveness probe; new `/health/redis` (readiness) and `/health/detailed`
  (DB + Redis + which optional integrations — AI, email, billing, Sentry,
  Langfuse — are actually configured, all in one call, handy for
  confirming a deployment's `.env` without grepping it).
- **8 passing, genuinely-executed unit tests** — unlike Phases 12-15,
  where the sandbox's lack of network access meant only `py_compile` plus
  a scratch-extracted copy of the pure logic could be verified, this
  phase's dependency-free core (`request_context.py`,
  `logging_config.JSONFormatter`) meant `tests/test_observability.py`
  could be imported and run for real, right here — see "Testing Phase
  16" for the actual output. `ai_client.py`'s text-extraction and
  Langfuse-configured-check logic (which *does* need the `anthropic`
  package to import) was additionally verified the scratch-extraction
  way, same as prior phases.

## What Phase 16 Deliberately Does NOT Include

- **Distributed tracing / OpenTelemetry spans across services** — this is
  a single FastAPI service with no internal service-to-service calls to
  trace; request-id correlation through logs covers this codebase's
  actual shape. An OTel exporter is a reasonable future addition if this
  ever becomes multiple services.
- **Metrics/Prometheus `/metrics` endpoint** — request counts and
  latencies are in the structured request logs (queryable in whatever
  aggregates them); a dedicated metrics-scrape endpoint with
  histograms/counters is a reasonable next step but wasn't built here to
  keep this phase's surface area honest about what was actually tested.
- **Alerting rules/dashboards** — configuring Sentry alert rules or
  building a Langfuse/Grafana dashboard is deployment/ops configuration,
  not application code.
- **PII scrubbing beyond `send_default_pii=False`** — Sentry's default
  request-body capture is off, but log lines themselves (e.g. an AI
  query's free-text `question`) are not automatically redacted. Add
  field-level redaction to `JSONFormatter` if a specific field turns out
  to need it.
- **Log shipping/retention configuration** — `JSONFormatter` writes to
  stdout via a `StreamHandler`; wiring that into an actual aggregator
  (a sidecar, a CloudWatch agent, ...) is a deployment concern (Phase 18).

## Testing Phase 16

1. No new migration this phase.
2. New dependencies — `pip install -r requirements.txt` now also installs
   `sentry-sdk==2.17.0` and `langfuse==2.53.9`.
3. `cd apps/api && python tests/test_observability.py` — 8 unit tests.
   > **These actually ran, for real, in this sandbox** — unlike Phases
   > 12-15's test files, which needed `fastapi`/`sqlalchemy`/etc. that
   > couldn't be installed here (no outbound network this session).
   > `app/core/request_context.py` and `logging_config.py`'s
   > `JSONFormatter` were written to have zero third-party imports at
   > module load specifically so this file could import them directly —
   > and it did. Actual output from this session:
   > ```
   > All Phase 16 unit tests passed.
   > ```
   > `app/services/ai_client.py`'s pure logic (text extraction from a
   > response, the Langfuse-configured check) needs the `anthropic`
   > package to import the real module, so *that* was verified the same
   > scratch-extraction way as prior phases (copied into a dependency-free
   > script, all assertions run and passed) rather than through the
   > delivered test file. Please still run
   > `pip install -r requirements.txt` and the real suite as your first
   > check.
4. Manual check, structured logging: start the API, hit any endpoint, and
   confirm stdout shows one JSON object per line including a
   `request_completed` entry with `http_method`, `http_path`,
   `http_status`, `duration_ms`, and `request_id`.
5. Manual check, request correlation: send a request with
   `-H "X-Request-ID: my-custom-id"`; confirm the response has
   `X-Request-ID: my-custom-id` and the corresponding log line does too.
   Send one without the header; confirm a fresh id is generated and used
   consistently across that request's log lines.
6. Manual check, Sentry graceful degradation: with `SENTRY_DSN` unset,
   trigger an unhandled exception (e.g. hit a route with a deliberately
   broken dependency) — confirm the API still returns a normal 500 and
   logs it, with nothing crashing at startup. Set a real (or Sentry
   test-project) DSN, repeat, and confirm the error shows up in Sentry.
7. Manual check, Langfuse tracing: with `LANGFUSE_PUBLIC_KEY`/
   `LANGFUSE_SECRET_KEY` unset, use the AI Query Engine (Phase 7) and
   confirm it still works normally. Set real Langfuse keys, repeat, and
   confirm a trace appears in the Langfuse dashboard tagged
   `nl_to_sql` with the prompt, completion, and token usage.
8. Manual check, health endpoints: `GET /health` (always `{"status":
   "ok"}`), `GET /health/redis` (stop Redis, confirm `"status":
   "degraded"` rather than a 500), `GET /health/detailed` (confirm
   `integrations_configured` accurately reflects your `.env`).

## What Phase 17 Delivers

- **pytest layered on top of the standalone scripts.** `pytest.ini`
  (markers `unit`, `integration`, `postgres`, `slow`; `--strict-markers`),
  `.coveragerc` (branch coverage of `app/`), and `requirements-dev.txt`
  (pytest, pytest-cov, faker — never installed in the production image).
  Every existing `python tests/test_<phase>.py` script keeps working.
- **Fakes for every optional external dependency** in `tests/fakes/`:
  `FakeAnthropicClient` (scripted queue or prompt-aware responder, call
  recording, simulated errors), `FakeStripe` (Customer / Checkout /
  Billing Portal / Webhook surface + a subscription-event builder), and
  `FakeSMTPFactory`/`FakeSMTPInbox`. Each mirrors exactly the call surface
  the app uses, so app code runs unmodified against them.
- **`tests/conftest.py`**: SQLite in-memory `db_session`, a `client`
  (TestClient) with `get_db`/`get_current_user` overridden but
  `require_workspace_role` left running for real, model factories, and
  `fake_ai_client` / `fake_stripe` / `smtp_inbox` / `override_settings`.
- **Integration tests** (`tests/integration/`): workspace isolation and
  role enforcement (404 vs 403), the Stripe checkout + webhook flow
  (including idempotency and bad signatures), the NL→SQL → `sql_guard`
  pipeline proving malicious AI output is rejected by code, and a
  Postgres-only RLS test that automates the manual check Phase 15 could
  only document.
- **CI** (`.github/workflows/tests.yml`): Postgres 16 + Redis 7 services,
  `alembic upgrade head`, sets a password on `app_runtime` (CI only), then
  the full suite with coverage.
- **`scripts/run_tests.sh`**: standalone scripts, then pytest with coverage.

## What Phase 17 Deliberately Does NOT Include

- **No new app features or migrations.** Only `main.py`'s version changed.
- **No fakes for Redis or S3/MinIO.** Cache and rate limiting already
  fail open, so tests don't need them; the AI query test targets the
  service layer rather than the `/query` route (which needs both).
- **No coverage threshold gate yet.** Coverage is reported, not enforced;
  pick a number once there's a real baseline from a CI run.
- **No frontend tests** (`apps/web`) and no load/performance testing.
- **SQLite is not Postgres.** `set_workspace_context` is a no-op in the
  SQLite fixtures; only the `postgres`-marked test proves real RLS.

## Testing Phase 17

**Honest status of verification:** this build environment had no network
and no fastapi/sqlalchemy/anthropic/stripe installed, so only the
dependency-free part could be executed here.

1. Executed here: `python tests/test_testing_infra.py` — 18/18 passed
   (the fakes' own behavior).
2. Everything else in Phase 17 was syntax-checked with `py_compile` but
   **not executed**: `conftest.py` and `tests/integration/*`. Expect to
   possibly fix small mismatches on the first real run.
3. On your machine:
   ```
   cd apps/api
   pip install -r requirements-dev.txt
   pytest -m "not postgres"          # no services needed
   bash scripts/run_tests.sh         # everything
   ```
   The `postgres` tests need `DATABASE_URL` and `APP_DB_URL` (see the CI
   workflow) and skip themselves otherwise.

## What Phase 18 Delivers

- **Production images.** `infra/docker/api.Dockerfile` (multi-stage,
  non-root, healthcheck, uvicorn with 2 workers and `--proxy-headers`) and
  `infra/docker/web.Dockerfile` (Next.js standalone output, non-root).
  `next.config.js` now sets `output: "standalone"`. `NEXT_PUBLIC_*` values
  are build args because Next inlines them into the bundle at build time.
- **`docker-compose.prod.yml`.** Postgres, Redis, MinIO, a one-shot
  `migrate` job, API, web, and Caddy (automatic HTTPS). Only Caddy
  publishes ports. The API waits for `migrate` to finish, and connects as
  the `app_runtime` role, so Phase 15's Row-Level Security is actually
  enforced in production instead of bypassed by an owner connection.
- **Automated `app_runtime` password.** `scripts/set_app_runtime_password.py`
  does the step Phase 15 left to the operator. It runs after
  `alembic upgrade head` in the migrate job and is safe to re-run.
- **Startup config checks** (`app/core/production_checks.py`). With
  `ENVIRONMENT=production` the API refuses to boot on critical problems:
  default or short `JWT_SECRET`, dev-default DB or S3 passwords, missing
  Clerk config. All problems are reported at once. Warnings (RLS not
  enforced, empty CORS, localhost `FRONTEND_BASE_URL`, no encryption key,
  Stripe key without webhook secret) are logged but never block. Dev and
  staging are never blocked.
- **`GET /health/ready`.** Returns 503 when the database is unreachable
  (Redis is not required, since caching and rate limiting fail open).
- **CI image builds** (`.github/workflows/build-images.yml`). Both images
  are built on every PR; on `main` and `v*` tags they are pushed to GHCR.
- **Ops helpers.** `.env.production.example`, `infra/scripts/backup_postgres.sh`,
  and a `.gitignore` covering `.env`, `.env.production`, and local data dirs.

## What Phase 18 Deliberately Does NOT Include

- **No cloud provisioning** (Terraform, Kubernetes manifests, ECS, Fly,
  ...). The output is images plus a single-host compose stack; where they
  run is your call. The images are 12-factor (config via env), so moving
  to an orchestrator later needs no app changes.
- **No automatic deploy job.** CI publishes images and stops; it does not
  SSH anywhere.
- **No zero-downtime rollout.** `up -d` recreates containers; expect a
  brief blip. Fine for a single host, not for an orchestrator.
- **No Postgres HA or managed backups.** Only a logical dump script.
- **No monitoring stack.** Sentry, Langfuse and structured logs from Phase
  16 are the observability story; run your own Prometheus/Grafana if wanted.
- **No `package-lock.json`.** The repo has none, so the web image falls
  back to `npm install`. Run `npm install` in `apps/web` once and commit
  the lockfile for reproducible builds (the Dockerfile then uses `npm ci`).
- **Existing Phase 17 note still applies:** the Phase 17 integration tests
  have not been executed against real dependencies.

## Testing Phase 18

**Honest status of verification:** no Docker daemon, no network, and no
fastapi/sqlalchemy in the build sandbox.

1. Executed here: `python tests/test_deployment.py` (38/38) covers the
   config-check logic and static validation of the artifacts (compose
   structure, every compose env var maps to a real `Settings` field, env
   template completeness, non-root Dockerfiles, only Caddy publishes
   ports, web build args match the Dockerfile, Caddy body limit above the
   upload limit). `python tests/test_testing_infra.py` (18/18) still passes.
   YAML and shell syntax were checked and all Python compiles.
2. **NOT executed:** no image was built and the stack was never booted. The
   Dockerfiles, compose file, Caddy config, the `/health/ready` route, the
   `set_app_runtime_password.py` script and the GitHub workflow are
   unverified beyond syntax. Expect to fix small things on the first
   real build.
3. Smoke test on a real host:
   ```
   cp .env.production.example .env.production   # fill in every value
   docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
   docker compose --env-file .env.production -f docker-compose.prod.yml logs migrate
   curl https://$API_DOMAIN/health/ready        # {"status":"ready"}
   curl https://$API_DOMAIN/health/detailed     # integrations_configured
   ```
   A wrong config shows up as the `api` container exiting with a
   "Refusing to start in production" message listing every critical problem.

## Deploying

First deploy: fill in `.env.production`, run the `up -d --build` command
above, confirm `/health/ready`. Update: `git pull`, then re-run `up -d
--build`; migrations run automatically in `migrate` before the API starts.
Rollback: redeploy the previous image tag (set `IMAGE_TAG`). Alembic
downgrades are not automatic, so restore from a backup if a migration
must be undone.

## Backups and restore

`bash infra/scripts/backup_postgres.sh ./backups` writes a compressed
custom-format dump (run it from cron and copy dumps off-host). Restore into
a running stack:
```
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backups/<file>.dump
```
Uploaded files live in the `miniodata` volume (or your S3 bucket) and are
not covered by the Postgres dump.

## What Phase 19 Delivers

Three enterprise features, all organization-level, owner-only (same
`require_organization_owner` rule as billing), and gated to the
`enterprise` plan through `FEATURE_MATRIX` in `app/services/enterprise.py`
(unknown plans get nothing).

- **API keys** (`/organizations/{id}/api-keys`). Format
  `aida_<8 hex>_<secret>`; the full key is returned exactly once and only
  its SHA-256 (plus the prefix, for lookup) is stored. Each key is scoped
  to one organization, is `read` or `read_write`, and always expires
  (default 90 days, max 365). A key acts as the user who created it:
  `require_workspace_role` still runs, so removing that user from a
  workspace removes the key's access to it. Keys are accepted **only** on
  routes with a `workspace_id` path parameter, inside their own
  organization, so they cannot reach billing, key management, or the
  allowlist, and cannot mint more keys. Wrong, unknown, revoked and
  expired keys all get the same generic 401. A downgraded organization's
  keys stop working but are not deleted (re-upgrading restores them), and
  listing and revoking stay available after a downgrade.
- **IP allowlist** (`PUT /organizations/{id}/security/ip-allowlist`).
  CIDR ranges, validated and canonicalised; `0.0.0.0/0` is rejected as a
  no-op. Enforced in `require_workspace_role` after the membership check,
  for session and key traffic alike. A non-empty list fails closed on an
  unknown client address. Two lockout guards: you cannot save a list that
  excludes your own current address, and org-level routes are never
  IP-restricted, so an owner on a blocked network can still clear the list.
- **Audit log export** (`GET /organizations/{id}/audit-log/export`).
  CSV or JSON across all of the organization's workspaces, with `since` /
  `until` filters, capped at 50,000 rows (`X-Export-Truncated` says if
  the cap was hit). CSV cells beginning with `= + - @` are prefixed with
  an apostrophe so opening the export in Excel or Sheets can't execute
  attacker-influenced text as a formula.
- **Also:** migration `0011` (`api_keys` table, `organizations.ip_allowlist`),
  `GET /organizations/{id}/enterprise/features`, structured `app.audit`
  log lines for key creation/revocation, allowlist changes and exports,
  and a Postgres test for a suspected latent Phase 15 issue (below).

## What Phase 19 Deliberately Does NOT Include

- **No SSO/SAML/SCIM.** Identity is delegated to Clerk, which offers
  enterprise SSO on its own plans; building a second implementation here
  would duplicate it.
- **No custom roles.** The four workspace roles are unchanged.
- **No audit-log retention or purge.** The activity log is documented as
  append-only since Phase 12; deleting from it conflicts with being an
  audit trail. Archive by exporting.
- **Org-level actions are not written to `activity_logs`.** That table
  requires a `workspace_id`; key/allowlist/export events go to the
  structured `app.audit` logger instead. Ship those logs somewhere durable
  if you need them as evidence.
- **No frontend UI.** These are API-only; `apps/web` is untouched.
- **Existing activity coverage is unchanged.** Only Phase 12's team
  actions write to the activity log, so the export contains only those.
- **Only trust `X-Forwarded-For` behind your proxy.** The allowlist reads
  the address uvicorn resolves; Phase 18 runs it with
  `--forwarded-allow-ips "*"`, which is safe only because in the compose
  stack the API is reachable solely through Caddy.

## Testing Phase 19

**Honest status of verification:** no fastapi, sqlalchemy or Docker in the
build sandbox.

1. **Executed here:** `python tests/test_enterprise.py` (53/53, all
   checks: key generation/verification, expiry, scope, throttling, IP
   matching including IPv4-mapped IPv6 and fail-closed behavior, CSV
   injection, and static consistency checks: migration chain and columns
   match the model, enforcement hooks are wired in the right order, every
   route is owner-gated, only `create` returns the full key). The Phase 17
   (18/18) and Phase 18 (38/38) standalone tests still pass, and all Python
   compiles.
2. **NOT executed:** `tests/integration/test_enterprise_flow.py` (27 route-level
   tests), the `deps.py` changes, the routes, and migration 0011 were only
   syntax-checked. Expect small fixes on the first real run.
3. **A bug found by reading, not by running:** SQLite returns timezone-naive
   datetimes, which would have raised `TypeError` when compared with an
   aware `now`. Fixed and tested (`_as_utc`).
4. **Suspected pre-existing Phase 15 issue, now covered by a test.** RLS
   policies read `current_setting('app.current_workspace_id', true)`. On a
   pooled connection that already ran a workspace-scoped transaction,
   Postgres may report the reset setting as an empty string rather than
   NULL, and `''::uuid` raises. Org-level queries run with no context set,
   so they would fail on such a connection. I could not confirm this
   without Postgres, so
   `test_unset_context_on_a_reused_connection_does_not_error` in
   `tests/integration/test_rls_postgres.py` checks it in CI. If it fails,
   the fix is a migration wrapping those reads in
   `NULLIF(current_setting(...), '')`.
5. On your machine:
   ```
   cd apps/api
   pytest tests/integration/test_enterprise_flow.py -m integration
   pytest -m postgres        # needs DATABASE_URL + APP_DB_URL, see CI workflow
   ```

## Project Status

All 19 phases are delivered. Suggested follow-ups, none started: run the
never-executed pieces (Phase 17 integration tests, Phase 18 image builds
and compose stack, Phase 19 flows) on a machine with the dependencies and
fix what surfaces; commit `package-lock.json`; add a coverage threshold to
CI once there is a baseline; and a frontend for the enterprise settings.
