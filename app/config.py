"""Settings, read from environment variables each time get_settings() is called.

Secrets only ever come from the environment (see .env.example for the names).
"""

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DATABASE_URL = "sqlite:///./second_wind.db"
DEFAULT_APP_TIMEZONE = "America/Sao_Paulo"

# Neon (and Heroku-style hosts) hand out plain postgres:// URLs; SQLAlchemy needs the driver named.
_POSTGRES_SCHEMES = ("postgres://", "postgresql://")
_PSYCOPG_SCHEME = "postgresql+psycopg://"


@dataclass(frozen=True)
class Settings:
    # repr=False: the URL holds the database password, and the webhook secret is a secret.
    database_url: str = field(repr=False)
    github_webhook_secret: str | None = field(repr=False)
    github_username: str | None
    app_timezone: ZoneInfo


def normalize_database_url(url: str) -> str:
    """Point plain Postgres URLs at the psycopg (v3) driver; leave anything else unchanged."""
    for scheme in _POSTGRES_SCHEMES:
        if url.startswith(scheme):
            return _PSYCOPG_SCHEME + url.removeprefix(scheme)
    return url


def _env(name: str) -> str | None:
    """An environment variable with surrounding whitespace removed; empty counts as unset."""
    value = os.environ.get(name, "").strip()
    return value or None


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"APP_TIMEZONE is not a valid IANA time zone: {name!r}") from exc


def get_settings() -> Settings:
    return Settings(
        database_url=normalize_database_url(_env("DATABASE_URL") or DEFAULT_DATABASE_URL),
        github_webhook_secret=_env("GITHUB_WEBHOOK_SECRET"),
        github_username=_env("GITHUB_USERNAME"),
        app_timezone=_timezone(_env("APP_TIMEZONE") or DEFAULT_APP_TIMEZONE),
    )
