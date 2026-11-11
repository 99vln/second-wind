"""Database models, in three layers kept apart:

- Event: the raw fact from a source, stored first and never changed (a GitHub commit).
- Win: how an event was interpreted, with what it granted at the time, under which rule version.
- ReserveRelease: Reserve XP paid out into total XP, triggered by the first win of a day.

Derived totals (total XP, Reserve balance, level) are sums over these rows, never stored
separately and never recomputed from events, so a rule change cannot lower what was granted.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names, so Alembic can find and change them later (SQLite needs this).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator[datetime]):
    """A timezone-aware timestamp, stored and returned in UTC on every backend.

    SQLite has no time zone type: a plain DateTime(timezone=True) silently drops the offset
    there. This type refuses naive datetimes and always hands back aware UTC ones.
    """

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: UTCDateTime(),
        dict[str, Any]: JSON(),
    }


class Event(Base):
    """A raw fact from a source, exactly as received."""

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # "github"
    external_id: Mapped[str] = mapped_column(String(255))  # commit SHA
    repo: Mapped[str] = mapped_column(String(255))  # "owner/name"
    author_username: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime]  # commit timestamp
    received_at: Mapped[datetime] = mapped_column(default=utcnow)
    payload: Mapped[dict[str, Any]]  # the raw commit object


class Win(Base):
    """An interpreted event and what it granted at the time. Never updated or deleted."""

    __tablename__ = "wins"
    __table_args__ = (
        CheckConstraint("xp >= 0", name="xp_not_negative"),
        CheckConstraint("energy_spent >= 0", name="energy_spent_not_negative"),
        CheckConstraint("reserve_deposit >= 0", name="reserve_deposit_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(32))  # "commit"
    local_day: Mapped[date] = mapped_column(index=True)  # day in APP_TIMEZONE
    xp: Mapped[int]  # XP granted immediately
    energy_spent: Mapped[int]
    reserve_deposit: Mapped[int]  # XP put into the Reserve instead of granted now
    rule_version: Mapped[int]
    earned_at: Mapped[datetime] = mapped_column(default=utcnow)


class ReserveRelease(Base):
    """Reserve XP moved into total XP, triggered by the first win of a local day."""

    __tablename__ = "reserve_releases"
    __table_args__ = (CheckConstraint("xp > 0", name="xp_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    win_id: Mapped[int] = mapped_column(ForeignKey("wins.id"), unique=True)
    local_day: Mapped[date] = mapped_column(index=True)
    xp: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
