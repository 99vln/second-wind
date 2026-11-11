from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.db import create_db_engine
from app.models import Base
from app.services.wins import RecordResult, record_commit

# 2026-11-10 12:00 in Sao Paulo (UTC-3, no DST).
NOON_DAY0 = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)

Recorder = Callable[..., RecordResult]


def noon(day_offset: int = 0, minute: int = 0) -> datetime:
    """Noon (Sao Paulo) `day_offset` days after 2026-11-10, plus `minute` minutes."""
    return NOON_DAY0 + timedelta(days=day_offset, minutes=minute)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def tz() -> ZoneInfo:
    return ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def record(session: Session, tz: ZoneInfo) -> Recorder:
    """record(when, sha=None, payload=None) -> RecordResult, with an auto-unique SHA."""
    counter = count(1)

    def _record(
        when: datetime, sha: str | None = None, payload: dict[str, Any] | None = None
    ) -> RecordResult:
        return record_commit(
            session,
            sha=sha or f"sha{next(counter):06d}",
            repo="octo/repo",
            author_username="octo",
            occurred_at=when,
            payload=payload if payload is not None else {"message": "msg"},
            tz=tz,
        )

    return _record
