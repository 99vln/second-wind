# 0001. Python end to end: FastAPI, HTMX, Postgres on Neon, Vercel

- Status: Accepted
- Date: 2026-11-10 (stack), 2026-11-11 (hosting)

## Context

Second Wind v0.1 is small: receive a GitHub push webhook, store each commit as an event, turn events into XP with a simple rule, and show XP and level on a page that can be installed as a desktop app (PWA). The project is also an AI engineering portfolio piece, so when AI comes in later it should land where evals and experiments are easiest to build and show. The budget is $0, and GitHub must be able to reach a public URL at any time to deliver webhooks.

## Options considered

| | A. Python + HTMX | B. Full-stack TypeScript | C. Python backend + React |
| --- | --- | --- | --- |
| Backend | FastAPI, SQLAlchemy, Alembic | Next.js API routes | FastAPI, SQLAlchemy, Alembic |
| Frontend | Server-rendered HTML + HTMX, PWA via manifest and service worker | React (Next.js) | React + Vite |
| Languages in v0.1 | 1 | 1 | 2 |
| AI signal | Strong | Medium | Strong |
| Weak spot | Less rich UI | Eval tooling is weaker | More moving parts before there is a product |

## Decision

Option A, Python end to end:

- **Backend:** FastAPI. Database access with SQLAlchemy and migrations with Alembic, added with the first model in v0.1.
- **Frontend:** server-rendered Jinja2 templates with HTMX, installable as a PWA through a web app manifest and a minimal service worker.
- **Database:** managed Postgres on Neon (free plan).
- **Hosting:** Vercel Hobby, running FastAPI as Python functions. No sleep on idle, so webhooks answer within GitHub's 10 s limit. Render's free web service is the fallback.
- **Tooling:** uv, ruff (including the `S` security rules), pytest, GitHub Actions.

**Layering rule, from the first commit.** All logic (events, XP, level) lives in a service layer (`app/services`) that knows nothing about HTTP or HTML. It is exposed through JSON routes under `/api` (`app/api`). The HTMX routes (`app/web`) are a thin shell that calls the service layer and renders HTML. Tests cover the service layer and the API, not the HTML.

## Consequences

- One language keeps the first working version close, and AI work later lands in the Python ecosystem.
- The UI is less rich than React. If the game shell needs heavy interactivity, the frontend can move to React (option C) while FastAPI keeps serving the same `/api` routes, so the backend is not rewritten. The layering rule is what keeps that exit open.
- Everything stays portable: a standard FastAPI app and plain Postgres. Moving off Vercel or Neon is a configuration change, not a rewrite.
- Vercel Hobby is for non-commercial use. Charging money later means moving to a paid plan or another host.
