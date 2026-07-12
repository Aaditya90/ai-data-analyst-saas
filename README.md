# AI Data Analyst SaaS Platform

Production-grade, workspace-based AI Data Analyst platform. Built **phase-wise** —
each phase is merged into `main` only after review, so `main` is always in a
deployable state.

## Phase Status

| # | Phase | Status |
|---|-------|--------|
| 1 | Foundation (monorepo, workspace-aware schema, docker dev env) | ✅ Complete |
| 2 | Authentication (Clerk + workspace RBAC) | ✅ Complete |
| 3 | Data Ingestion | ⏳ Not started |
| 4 | Data Cleaning Engine | ⏳ Not started |
| 5 | EDA Engine | ⏳ Not started |
| 6 | Dashboard Builder | ⏳ Not started |
| 7 | AI Query Engine | ⏳ Not started |
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

## Next Phase

**Phase 3: Data Ingestion** — will NOT start until this phase is reviewed,
pushed, and you explicitly say "start phase 3."
