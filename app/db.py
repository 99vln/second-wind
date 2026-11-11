"""Database engine and sessions.

Nothing connects at import time: the engine is built from DATABASE_URL on first use, so the app
(and its tests) can be imported without a reachable database.
"""

from collections.abc import Iterator
from functools import cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


def create_db_engine(url: str, **kwargs: Any) -> Engine:
    """Create an engine with the per-backend settings Second Wind relies on.

    Used by the app, by Alembic and by the tests, so all three behave the same way.
    """
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        # Neon closes idle connections when it scales to zero; check before reusing one.
        kwargs.setdefault("pool_pre_ping", True)
        return create_engine(url, **kwargs)

    connect_args = kwargs.setdefault("connect_args", {})
    # FastAPI may run a sync dependency and its route on different worker threads.
    connect_args.setdefault("check_same_thread", False)
    if parsed.database in (None, "", ":memory:"):
        # An in-memory database lives inside one connection, so every session must share it.
        kwargs.setdefault("poolclass", StaticPool)
    engine = create_engine(url, **kwargs)
    event.listen(engine, "connect", _on_sqlite_connect)
    event.listen(engine, "begin", _on_sqlite_begin)
    return engine


def _on_sqlite_connect(dbapi_connection: Any, _connection_record: Any) -> None:
    # Python's sqlite3 driver manages transactions itself and gets SAVEPOINT wrong; turn that
    # off and emit BEGIN from SQLAlchemy instead (the recipe from the SQLAlchemy SQLite docs).
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys unless asked
    cursor.close()


def _on_sqlite_begin(connection: Any) -> None:
    # IMMEDIATE takes the write lock up front, so concurrent writers wait their turn instead
    # of failing with "database is locked" (the job the Postgres advisory lock does there).
    connection.exec_driver_sql("BEGIN IMMEDIATE")


@cache
def get_engine() -> Engine:
    return create_db_engine(get_settings().database_url)


@cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a session and close it afterwards (usable as a FastAPI dependency).

    The caller commits; anything not committed is rolled back when the session closes.
    """
    with get_sessionmaker()() as session:
        yield session
