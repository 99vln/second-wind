# 0002. Persistence with SQLAlchemy and Alembic, and XP as a stored ledger

- Status: Accepted
- Date: 2026-11-11

## Context

v0.1 turns GitHub commits into XP. That needs a database, a way to change its schema safely, and a decision on how XP is kept. The product rule says XP, levels and progress are never taken away (see `.claude/CLAUDE.md`), and the XP rule is expected to change as we learn what feels good. The first rule, "Energy", is described below.

## Options considered

How XP is kept:

| | A. Recompute from events | B. Ledger: store what each win granted |
| --- | --- | --- |
| Source of truth | Raw events plus the current rule | Win and ReserveRelease rows, each with a `rule_version` |
| Rule change | Rewrites all past XP, up or down | Only applies to commits recorded afterwards |
| Product rule ("never take away") | Can be broken by a stricter rule | Holds by construction |
| Cost | Simple writes, heavier reads | One extra table, sums on read |

Database tooling: SQLAlchemy 2.0 (typed models) with Alembic migrations, as planned in ADR 0001, versus raw SQL. Raw SQL would mean hand-written migrations and string handling we would rather not own.

## Decision

- **SQLAlchemy 2.0 and Alembic.** Models in `app/models.py` (typed `Mapped[...]` style), engine and sessions in `app/db.py`, migrations in `alembic/`. The URL always comes from `DATABASE_URL` (`app/config.py`), never from `alembic.ini`. Postgres uses the psycopg 3 driver; plain `postgres://` URLs from Neon are rewritten to `postgresql+psycopg://`.
- **Postgres on Neon in production, SQLite in tests and local development.** Tests use an in-memory SQLite database created per test. Timestamps go through a small `UTCDateTime` type that stores UTC and refuses naive datetimes, because SQLite would otherwise drop the time zone silently.
- **Option B, a ledger.** Three layers, kept apart: `Event` (the raw commit, stored first, unique on source and SHA), `Win` (what the event granted: XP, Energy spent, Reserve deposit, `rule_version`, local day) and `ReserveRelease` (Reserve XP paid out). Total XP is `sum(Win.xp) + sum(ReserveRelease.xp)`; the Reserve balance is `sum(Win.reserve_deposit) - sum(ReserveRelease.xp)`. Nothing already granted is updated or recomputed. Commits are counted in the order they arrive.
- **Writes take turns.** On Postgres, recording a commit takes a transaction-scoped advisory lock, so two webhook deliveries handled at once cannot both spend the day's last Energy or both release the same Reserve XP. A repeated SHA is caught by the unique constraint, also when two requests race.
- **`tzdata` as a dependency.** Local days are computed with `zoneinfo`, which needs a time zone database. Windows and some minimal Linux images have none, so the `tzdata` package is a direct dependency.

### The Energy rule (rule_version 1)

Energy works like stamina in games: it paces progress so a hyperfocus binge cannot burn through the game in a few days, but real work is never wasted. All numbers are constants in `app/services/xp.py`.

| Constant | Start value | Meaning |
| --- | --- | --- |
| `ENERGY_PER_DAY` | 100 | Daily bar, refilled at midnight in `APP_TIMEZONE` |
| `COMMIT_ENERGY_COST` / `COMMIT_XP` | 10 / 10 | Each commit spends 10 Energy and grants 10 XP |
| `RESERVE_DEPOSIT_XP` | 5 | A commit made with less than 10 Energy left grants 0 XP now and puts 5 XP in the Reserve |
| `RESERVE_DAILY_RELEASE_XP` | 30 | The first commit of each local day releases up to 30 XP from the Reserve (never more than the balance) |
| `SECOND_WIND_GAP_DAYS` / `SECOND_WIND_ENERGY` | 3 / 200 | When the last day with a commit was 3 or more days ago, the bar is 200 (the first day ever uses 100) |
| `LEVEL_XP_FACTOR` | 100 | Level L needs 100 * L² total XP: 0-99 is level 0, 100-399 level 1, 400-899 level 2 |

Unused Energy does not roll over. The Reserve is never lost: hyperfocus days fund the slow days.

## Consequences

- A rule change is a new `RULE_VERSION` plus new constants or code; past wins keep what they granted. If a future rule needs a one-off adjustment, it is added as new rows, never by editing old ones.
- Totals are sums over all rows. That is fine for one user; if it gets slow, a cached total can be added later and checked against the ledger.
- Tests run on SQLite, production on Postgres. The models stay portable (no Postgres-only types), and the Postgres-only advisory lock is skipped on SQLite, which allows a single writer anyway. The first migration was checked by hand and rendered for Postgres offline; it has not yet been run against Neon.
- Commits arriving out of order (for example an old branch pushed late) count for the day of their own timestamp, using the state stored at the time they arrive. They are never recounted later.
