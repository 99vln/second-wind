"""The XP rule ("Energy"): pure functions and tunable constants, no database access.

Energy works like stamina in games. It paces progress so a hyperfocus binge cannot burn
through the game in a few days, but real work is never wasted:

- Each local day has an Energy bar, refilled at midnight in APP_TIMEZONE. Unused Energy does
  not carry over.
- A commit spends COMMIT_ENERGY_COST Energy and grants COMMIT_XP XP.
- A commit made with too little Energy left grants no XP now; it deposits RESERVE_DEPOSIT_XP
  into the Reserve instead. The Reserve is never lost.
- The first commit of each local day releases up to RESERVE_DAILY_RELEASE_XP from the Reserve
  into total XP, so hyperfocus days fund the slow days.
- Second Wind: after SECOND_WIND_GAP_DAYS or more days without a commit, the bar is bigger.
- Reaching level L takes LEVEL_XP_FACTOR * L**2 total XP.

Granted XP is stored with RULE_VERSION and never recomputed: changing a number here (and
bumping RULE_VERSION) only affects commits recorded afterwards.
"""

from dataclasses import dataclass
from datetime import date, datetime
from math import isqrt
from zoneinfo import ZoneInfo

RULE_VERSION = 1

ENERGY_PER_DAY = 100
COMMIT_ENERGY_COST = 10
COMMIT_XP = 10

RESERVE_DEPOSIT_XP = 5
RESERVE_DAILY_RELEASE_XP = 30

SECOND_WIND_GAP_DAYS = 3
SECOND_WIND_ENERGY = 200

LEVEL_XP_FACTOR = 100


@dataclass(frozen=True)
class DayState:
    """What is already stored for the commit's local day, before this commit is counted."""

    energy_cap: int
    energy_spent_today: int
    reserve_balance: int
    is_first_commit_of_day: bool


@dataclass(frozen=True)
class CommitGrant:
    """What one commit grants. All values are zero or positive."""

    xp: int
    energy_spent: int
    reserve_deposit: int
    reserve_release: int


@dataclass(frozen=True)
class LevelProgress:
    total_xp: int
    level: int
    xp_into_level: int  # XP earned since reaching the current level
    xp_for_next_level: int  # size of the current level band
    xp_to_next_level: int  # XP still needed for the next level


def energy_cap_for_day(day: date, last_active_day: date | None) -> int:
    """The day's Energy bar: bigger after a break (Second Wind), normal on the first day ever.

    last_active_day is the most recent day before `day` with a commit win.
    """
    if last_active_day is not None and (day - last_active_day).days >= SECOND_WIND_GAP_DAYS:
        return SECOND_WIND_ENERGY
    return ENERGY_PER_DAY


def grant_for_commit(state: DayState) -> CommitGrant:
    # The release uses the balance before this commit's own deposit, and can never exceed it.
    release = 0
    if state.is_first_commit_of_day:
        release = max(0, min(RESERVE_DAILY_RELEASE_XP, state.reserve_balance))

    energy_left = state.energy_cap - state.energy_spent_today
    if energy_left >= COMMIT_ENERGY_COST:
        return CommitGrant(
            xp=COMMIT_XP,
            energy_spent=COMMIT_ENERGY_COST,
            reserve_deposit=0,
            reserve_release=release,
        )
    return CommitGrant(
        xp=0,
        energy_spent=0,
        reserve_deposit=RESERVE_DEPOSIT_XP,
        reserve_release=release,
    )


def xp_for_level(level: int) -> int:
    """Total XP needed to reach `level`."""
    if level < 0:
        raise ValueError("level cannot be negative")
    return LEVEL_XP_FACTOR * level * level


def level_for_xp(total_xp: int) -> int:
    """The highest level L with LEVEL_XP_FACTOR * L**2 <= total_xp, in integer math."""
    if total_xp < 0:
        raise ValueError("total_xp cannot be negative")
    # isqrt(floor(x)) == floor(sqrt(x)) for x >= 0, so no float rounding can creep in.
    return isqrt(total_xp // LEVEL_XP_FACTOR)


def progress_for_xp(total_xp: int) -> LevelProgress:
    level = level_for_xp(total_xp)
    level_start = xp_for_level(level)
    next_level_start = xp_for_level(level + 1)
    return LevelProgress(
        total_xp=total_xp,
        level=level,
        xp_into_level=total_xp - level_start,
        xp_for_next_level=next_level_start - level_start,
        xp_to_next_level=next_level_start - total_xp,
    )


def local_day(ts: datetime, tz: ZoneInfo) -> date:
    """The calendar day `ts` falls on in time zone `tz`."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(tz).date()
