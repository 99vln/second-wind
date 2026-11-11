# Second Wind: project rules for Claude Code

Second Wind is a real-life game for people with ADHD: wins that already happen (GitHub commits first) become XP, levels and quests. The product is built for the drop in interest after hyperfocus, not against it.

## Product rule that overrides everything

**The app never punishes.** It may withhold a reward, but it never takes away XP, levels or progress. No day streaks, no counting missed days, no guilt in copy. A break is followed by a comeback bonus.

## Architecture

- `app/services/`: all domain logic (events, wins, rewards, XP, level). No FastAPI, no HTML.
- `app/api/`: JSON routes under `/api` that only call the service layer.
- `app/web/` + `app/templates/`: thin HTMX shell that calls the service layer and renders HTML. No logic.
- Data model keeps three layers apart: **event** (raw fact from a source) → **win** (interpreted event) → **reward** (XP, level, quest). Store the raw event first, then apply rules, so rules can change without losing history.
- Decisions are recorded as ADRs in `docs/decisions/`. Add one for any new technical choice.

## Commands

```sh
uv sync                       # install
uv run uvicorn app.main:app --reload
uv run pytest                 # tests
uv run ruff check .           # lint, includes security rules (S)
uv run ruff format .          # format
```

## Security baseline

- Secrets only in environment variables. Never commit `.env`; add new variable names (no values) to `.env.example`.
- Webhooks: verify the HMAC signature with `hmac.compare_digest`, reject requests without one, stay idempotent on the commit SHA.
- No SQL built from strings; no unescaped user input in HTML.
- Run the `security-reviewer` subagent before every PR. Use `test-writer` with every new feature.

## Conventions

- Code, file names, comments and docs in English.
- Each piece of work goes through its own branch and PR; `main` only receives green PRs.
- Tests cover the service layer and `/api`, not the HTML.
