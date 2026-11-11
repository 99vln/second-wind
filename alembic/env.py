"""Alembic environment for Second Wind.

The database URL comes from DATABASE_URL via app.config (never from alembic.ini), and the
schema from app.models, so `alembic revision --autogenerate` compares against the models.
"""

from logging.config import fileConfig
from typing import Any

from sqlalchemy import pool
from sqlalchemy.engine import make_url

from alembic import context
from app.config import get_settings
from app.db import create_db_engine
from app.models import Base, UTCDateTime

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Write UTCDateTime as a plain timestamptz column, so migrations never import app code."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def _is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def run_migrations_offline() -> None:
    """Emit the migration SQL as a script, without connecting."""
    url = get_settings().database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
        render_as_batch=_is_sqlite(url),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_settings().database_url
    engine = create_db_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
            # SQLite cannot ALTER most things; batch mode rebuilds the table instead.
            render_as_batch=_is_sqlite(url),
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
