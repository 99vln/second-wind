"""Recording commits as wins, and reading the totals back.

Functions take a SQLAlchemy Session and know nothing about HTTP. The caller decides which
commits count (for example, only GITHUB_USERNAME's) and commits the transaction; this module
only records what it is given. Commits are counted in the order they are recorded, and nothing
already granted is ever recomputed.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
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
    local_day,
)

SOURCE_GITHUB = "github"
KIND_COMMIT = "commit"

# Postgres advisory lock key that makes ledger writes take turns. Any fixed number works.
LEDGER_LOCK_KEY = 0x5357_0001


@dataclass(frozen=True)
class RecordResult:
    created: bool  # False when the SHA was already recorded: nothing was granted this time
    xp: int
    reserve_deposit: int
    reserve_release: int


_DUPLICATE = RecordResult(created=False, xp=0, reserve_deposit=0, reserve_release=0)


@dataclass(frozen=True)
class EnergyStatus:
    day: date
    energy_cap: int
    energy_spent: int
    energy_left: int


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
    payload: dict[str, Any],
    tz: ZoneInfo,
) -> RecordResult:
    """Store a commit as an Event and, if it is new, the Win (and Reserve release) it earns.

    A SHA that was already recorded grants nothing and returns created=False.
    """
    if not sha:
        raise ValueError("sha is required")
    day = local_day(occurred_at, tz)  # rejects naive timestamps before touching the database

    _lock_ledger(session)
    if _event_exists(session, sha):
        return _DUPLICATE

    try:
        with session.begin_nested():
            now = datetime.now(UTC)
            event = Event(
                source=SOURCE_GITHUB,
                external_id=sha,
                repo=repo,
                author_username=author_username,
                occurred_at=occurred_at,
                received_at=now,
                payload=payload,
            )
            session.add(event)
            session.flush()  # raw fact first; the unique (source, external_id) is the real guard

            grant = grant_for_commit(_day_state(session, day))
            win = Win(
                event_id=event.id,
                kind=KIND_COMMIT,
                local_day=day,
                xp=grant.xp,
                energy_spent=grant.energy_spent,
                reserve_deposit=grant.reserve_deposit,
                rule_version=RULE_VERSION,
                earned_at=now,
            )
            session.add(win)
            session.flush()

            if grant.reserve_release > 0:
                session.add(
                    ReserveRelease(
                        win_id=win.id, local_day=day, xp=grant.reserve_release, created_at=now
                    )
                )
                session.flush()
    except IntegrityError:
        # The savepoint is already rolled back. If another writer stored this SHA between
        # our check and our insert, that is a duplicate; any other violation is a real bug.
        if _event_exists(session, sha):
            return _DUPLICATE
        raise

    return RecordResult(
        created=True,
        xp=grant.xp,
        reserve_deposit=grant.reserve_deposit,
        reserve_release=grant.reserve_release,
    )


def get_energy(session: Session, day: date | None, tz: ZoneInfo) -> EnergyStatus:
    """Energy for `day` (today in `tz` when None). A day without commits shows its full cap,
    including Second Wind when it would apply."""
    if day is None:
        day = local_day(datetime.now(UTC), tz)
    cap = energy_cap_for_day(day, _last_active_day_before(session, day))
    _, spent = _commit_wins_on(session, day)
    return EnergyStatus(
        day=day, energy_cap=cap, energy_spent=spent, energy_left=max(0, cap - spent)
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
    SQLite (tests and local dev) allows a single writer anyway.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(LEDGER_LOCK_KEY)))


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
