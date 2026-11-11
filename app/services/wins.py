"""Recording commits as wins, and reading the totals back.

Functions take a SQLAlchemy Session and know nothing about HTTP. The caller decides which
commits count (for example, only GITHUB_USERNAME's) and commits the transaction; this module
only records what it is given. A commit counts for the local day on which it was received,
commits are counted in the order they are recorded, and nothing already granted is ever
recomputed.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Event, ReserveRelease, Win
from app.services.xp import (
    RULE_VERSION,
    DayState,
    energy_cap_for_day,
    grant_for_commit,
    is_second_wind_day,
    is_too_old,
    local_day,
)

SOURCE_GITHUB = "github"
KIND_COMMIT = "commit"

# Postgres advisory lock key that makes ledger writes take turns. Any fixed number works.
LEDGER_LOCK_KEY = 0x5357_0001


class Outcome(StrEnum):
    NEW = "new"  # stored and granted
    DUPLICATE = "duplicate"  # SHA already recorded: nothing stored or granted this time
    TOO_OLD = "too_old"  # older than MAX_COMMIT_AGE_DAYS when received: nothing stored


@dataclass(frozen=True)
class RecordResult:
    outcome: Outcome
    xp: int
    reserve_deposit: int
    reserve_release: int

    @property
    def created(self) -> bool:
        return self.outcome is Outcome.NEW


_DUPLICATE = RecordResult(Outcome.DUPLICATE, xp=0, reserve_deposit=0, reserve_release=0)
_TOO_OLD = RecordResult(Outcome.TOO_OLD, xp=0, reserve_deposit=0, reserve_release=0)


@dataclass(frozen=True)
class EnergyStatus:
    day: date
    energy_cap: int
    energy_spent: int
    energy_left: int
    second_wind: bool  # the bar is bigger today because of a break (Second Wind)


@dataclass(frozen=True)
class RecentWin:
    win_id: int
    kind: str
    repo: str
    sha: str
    message: str | None
    local_day: date
    xp: int
    reserve_deposit: int
    reserve_release: int  # Reserve XP this win released (first win of its day), else 0
    earned_at: datetime


def record_commit(
    session: Session,
    *,
    sha: str,
    repo: str,
    author_username: str | None,
    occurred_at: datetime,
    received_at: datetime,
    payload: dict[str, Any],
    tz: ZoneInfo,
) -> RecordResult:
    """Store a commit as an Event and, if it is new, the Win (and Reserve release) it earns.

    occurred_at is the commit's own timestamp, kept on the event; received_at is when the
    server received it, and decides the local day the commit counts for. A commit older than
    MAX_COMMIT_AGE_DAYS when received stores nothing (Outcome.TOO_OLD); a SHA that was already
    recorded grants nothing (Outcome.DUPLICATE).
    """
    if not sha:
        raise ValueError("sha is required")
    # Both checks reject naive timestamps before touching the database.
    if is_too_old(occurred_at, received_at):
        return _TOO_OLD
    day = local_day(received_at, tz)

    _lock_ledger(session)
    if _event_exists(session, sha):
        return _DUPLICATE

    # Outer savepoint: the event, win and release are stored together or not at all.
    with session.begin_nested():
        event = Event(
            source=SOURCE_GITHUB,
            external_id=sha,
            repo=repo,
            author_username=author_username,
            occurred_at=occurred_at,
            received_at=received_at,
            payload=payload,
        )
        if not _insert_event(session, event):  # raw fact first
            return _DUPLICATE

        grant = grant_for_commit(_day_state(session, day))
        win = Win(
            event_id=event.id,
            kind=KIND_COMMIT,
            local_day=day,
            xp=grant.xp,
            energy_spent=grant.energy_spent,
            reserve_deposit=grant.reserve_deposit,
            rule_version=RULE_VERSION,
            earned_at=received_at,
        )
        session.add(win)
        session.flush()

        if grant.reserve_release > 0:
            session.add(
                ReserveRelease(
                    win_id=win.id, local_day=day, xp=grant.reserve_release, created_at=received_at
                )
            )
            session.flush()

    return RecordResult(
        Outcome.NEW,
        xp=grant.xp,
        reserve_deposit=grant.reserve_deposit,
        reserve_release=grant.reserve_release,
    )


def get_energy(session: Session, day: date | None, tz: ZoneInfo) -> EnergyStatus:
    """Energy for `day` (today in `tz` when None). A day without commits shows its full cap,
    including Second Wind when it would apply."""
    if day is None:
        day = local_day(datetime.now(UTC), tz)
    last_active = _last_active_day_before(session, day)
    cap = energy_cap_for_day(day, last_active)
    _, spent = _commit_wins_on(session, day)
    return EnergyStatus(
        day=day,
        energy_cap=cap,
        energy_spent=spent,
        energy_left=max(0, cap - spent),
        second_wind=is_second_wind_day(day, last_active),
    )


def total_xp(session: Session) -> int:
    """XP granted by wins plus XP released from the Reserve."""
    granted = session.scalar(select(func.coalesce(func.sum(Win.xp), 0)))
    released = session.scalar(select(func.coalesce(func.sum(ReserveRelease.xp), 0)))
    return int(granted) + int(released)


def reserve_balance(session: Session) -> int:
    """XP deposited into the Reserve minus XP released from it."""
    deposited = session.scalar(select(func.coalesce(func.sum(Win.reserve_deposit), 0)))
    released = session.scalar(select(func.coalesce(func.sum(ReserveRelease.xp), 0)))
    return int(deposited) - int(released)


def recent_wins(session: Session, limit: int = 10) -> list[RecentWin]:
    """The latest wins, newest first, with the repo and commit message of their events."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    rows = session.execute(
        select(
            Win.id,
            Win.kind,
            Event.repo,
            Event.external_id,
            Event.payload["message"].as_string(),
            Win.local_day,
            Win.xp,
            Win.reserve_deposit,
            func.coalesce(ReserveRelease.xp, 0),
            Win.earned_at,
        )
        .join(Event, Win.event_id == Event.id)
        .outerjoin(ReserveRelease, ReserveRelease.win_id == Win.id)
        .order_by(Win.earned_at.desc(), Win.id.desc())
        .limit(limit)
    ).all()
    return [RecentWin(*row) for row in rows]


def _lock_ledger(session: Session) -> None:
    """On Postgres, make ledger writes take turns until the transaction ends.

    Without it, two webhook deliveries processed at once could both spend the day's last
    Energy, or both release the same Reserve XP and push the balance below zero.
    SQLite (tests and local dev) gets the same effect from BEGIN IMMEDIATE (see app/db.py).
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(LEDGER_LOCK_KEY)))


def _insert_event(session: Session, event: Event) -> bool:
    """Insert the event; False if another writer stored the same SHA first.

    The unique (source, external_id) constraint is the real guard against duplicates. Only an
    IntegrityError from this insert, with the SHA now present, counts as a duplicate; any
    other error propagates.
    """
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
    except IntegrityError:
        if _event_exists(session, event.external_id):
            return False
        raise
    return True


def _event_exists(session: Session, sha: str) -> bool:
    found = session.scalar(
        select(Event.id).where(Event.source == SOURCE_GITHUB, Event.external_id == sha)
    )
    return found is not None


def _commit_wins_on(session: Session, day: date) -> tuple[int, int]:
    """(number of commit wins, Energy they spent) on a local day."""
    count, spent = session.execute(
        select(func.count(Win.id), func.coalesce(func.sum(Win.energy_spent), 0)).where(
            Win.kind == KIND_COMMIT, Win.local_day == day
        )
    ).one()
    return int(count), int(spent)


def _last_active_day_before(session: Session, day: date) -> date | None:
    """The most recent local day before `day` with a commit win."""
    return session.scalar(
        select(func.max(Win.local_day)).where(Win.kind == KIND_COMMIT, Win.local_day < day)
    )


def _day_state(session: Session, day: date) -> DayState:
    wins_today, spent_today = _commit_wins_on(session, day)
    return DayState(
        energy_cap=energy_cap_for_day(day, _last_active_day_before(session, day)),
        energy_spent_today=spent_today,
        reserve_balance=reserve_balance(session),
        is_first_commit_of_day=wins_today == 0,
    )
