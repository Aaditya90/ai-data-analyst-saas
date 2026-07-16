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
| 8 | AI Insights | ⏳ Not started |
| 9 | AI Dashboard Generator | ⏳ Not started |
| 10 | ML & Forecasting / AutoML | ⏳ Not started |
| 11 | Reports (PDF/PPT) | ⏳ Not started |
| 12 | Team Collaboration | ⏳ Not started |
| 13 | Version History | ⏳ Not started |
| 14 | Billing | ⏳ Not started |
| 15 | Security | ⏳ Not started |
| 16 | Observability | ⏳ Not started |
| 17 | Testing | ⏳ Not started |
| 18 | Deployment | ⏳ Not started |
| 19 | Enterprise Features | ⏳ Not started |

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
- **Cache/Queue backbone**: Redis 7 (wired in now, used from Phase 10 onward)
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

## Next Phase

**Phase 8: AI Insights** — will NOT start until this phase is reviewed,
pushed, and you explicitly say "start phase 8."
