# Second Wind

A real-life game for people with ADHD, built for the moment interest drops.

## Why it exists

People with ADHD often hyperfocus on something new, then lose interest once the novelty fades. That drop is not a lack of willpower, and most habit apps make it worse: they become one more novelty, they depend on remembering to log things, and they punish a broken streak with guilt.

Second Wind is designed for that valley instead of against it. Wins you already have, like GitHub commits, are captured automatically and turned into XP, levels and quests. There are no day streaks and nothing is ever taken away: a break is followed by a comeback, not a penalty.

## Using it

Second Wind runs as a website you can install as a desktop app. Open it in Chrome or Edge and choose **Install app** from the address bar; it then opens in its own window with a taskbar icon.

The first version counts XP from GitHub commits for a single user. Connecting your repositories is described here once that version ships.

### Running it locally

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env   # fill in the values
uv run --env-file .env uvicorn app.main:app --reload
```

Then open http://localhost:8000. Run the tests with `uv run pytest`.

### Database

Production uses Postgres on [Neon](https://neon.tech) (free plan). Without `DATABASE_URL`, the app uses a local SQLite file (`second_wind.db`).

1. Create a Neon project and database, and copy its connection string.
2. Put it in `.env` as `DATABASE_URL=postgresql://...` (the plain form from Neon is fine).
3. Create the tables:

   ```sh
   uv run --env-file .env alembic upgrade head
   ```

`--env-file .env` is what loads `.env`: without it, nothing reads the file and the command runs against the local SQLite default.

How it is built and why: see [docs/](docs/).
