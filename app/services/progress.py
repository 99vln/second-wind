"""The player's current progress: XP and level, today's Energy, the Reserve and recent wins.

Read-only. Built from the ledger in app.services.wins; nothing here is stored.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.wins import get_energy, recent_wins, reserve_balance, total_xp
from app.services.xp import progress_for_xp

RECENT_WINS_LIMIT = 10
SHORT_SHA_LENGTH = 7


@dataclass(frozen=True)
class EnergyToday:
    day: date  # today in APP_TIMEZONE
    cap: int
    spent: int
    left: int
    second_wind: bool  # the bar is bigger today after a break


@dataclass(frozen=True)
class WinSummary:
    xp: int
    reserve_deposit: int
    repo: str
    sha: str  # short form
    message: str  # first line of the commit message
    earned_at: datetime


@dataclass(frozen=True)
class Progress:
    total_xp: int
    level: int
    xp_into_level: int
    xp_for_next_level: int
    xp_to_next_level: int
    energy: EnergyToday
    reserve_balance: int
    recent_wins: list[WinSummary]


def get_progress(session: Session, tz: ZoneInfo, today: date | None = None) -> Progress:
    """Progress as of `today` in `tz` (the current local day when None)."""
    level = progress_for_xp(total_xp(session))
    energy = get_energy(session, today, tz)
    return Progress(
        total_xp=level.total_xp,
        level=level.level,
        xp_into_level=level.xp_into_level,
        xp_for_next_level=level.xp_for_next_level,
        xp_to_next_level=level.xp_to_next_level,
        energy=EnergyToday(
            day=energy.day,
            cap=energy.energy_cap,
            spent=energy.energy_spent,
            left=energy.energy_left,
            second_wind=energy.second_wind,
        ),
        reserve_balance=reserve_balance(session),
        recent_wins=[
            WinSummary(
                xp=win.xp,
                reserve_deposit=win.reserve_deposit,
                repo=win.repo,
                sha=win.sha[:SHORT_SHA_LENGTH],
                message=_first_line(win.message),
                earned_at=win.earned_at,
            )
            for win in recent_wins(session, limit=RECENT_WINS_LIMIT)
        ],
    )


def _first_line(message: str | None) -> str:
    lines = (message or "").strip().splitlines()
    return lines[0].strip() if lines else ""
