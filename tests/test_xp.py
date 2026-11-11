from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.xp import (
    COMMIT_ENERGY_COST,
    COMMIT_XP,
    ENERGY_PER_DAY,
    RESERVE_DAILY_RELEASE_XP,
    RESERVE_DEPOSIT_XP,
    SECOND_WIND_ENERGY,
    SECOND_WIND_GAP_DAYS,
    DayState,
    energy_cap_for_day,
    grant_for_commit,
    level_for_xp,
    local_day,
    progress_for_xp,
    xp_for_level,
)


def state(cap=ENERGY_PER_DAY, spent=0, balance=0, first=False) -> DayState:
    return DayState(
        energy_cap=cap,
        energy_spent_today=spent,
        reserve_balance=balance,
        is_first_commit_of_day=first,
    )


def test_full_grant_with_exactly_one_commit_of_energy_left():
    grant = grant_for_commit(state(spent=ENERGY_PER_DAY - COMMIT_ENERGY_COST))
    assert (grant.xp, grant.energy_spent, grant.reserve_deposit) == (
        COMMIT_XP,
        COMMIT_ENERGY_COST,
        0,
    )


def test_one_energy_short_deposits_to_reserve():
    grant = grant_for_commit(state(spent=ENERGY_PER_DAY - COMMIT_ENERGY_COST + 1))
    assert (grant.xp, grant.energy_spent, grant.reserve_deposit) == (0, 0, RESERVE_DEPOSIT_XP)


def test_release_only_on_first_commit_of_day():
    assert grant_for_commit(state(balance=100, first=False)).reserve_release == 0
    first = grant_for_commit(state(balance=100, first=True))
    assert first.reserve_release == RESERVE_DAILY_RELEASE_XP


def test_release_is_capped_by_balance():
    assert grant_for_commit(state(balance=5, first=True)).reserve_release == 5


def test_negative_balance_releases_nothing():
    assert grant_for_commit(state(balance=-10, first=True)).reserve_release == 0


def test_release_happens_even_when_commit_has_no_energy():
    grant = grant_for_commit(state(spent=ENERGY_PER_DAY, balance=50, first=True))
    assert grant.xp == 0
    assert grant.reserve_release == RESERVE_DAILY_RELEASE_XP


def test_cap_first_day_ever_is_normal():
    assert energy_cap_for_day(date(2026, 11, 10), None) == ENERGY_PER_DAY


def test_cap_gap_below_threshold_is_normal():
    last = date(2026, 11, 10)
    assert energy_cap_for_day(date(2026, 11, 10 + SECOND_WIND_GAP_DAYS - 1), last) == (
        ENERGY_PER_DAY
    )


def test_cap_gap_at_threshold_is_second_wind():
    last = date(2026, 11, 10)
    assert energy_cap_for_day(date(2026, 11, 10 + SECOND_WIND_GAP_DAYS), last) == (
        SECOND_WIND_ENERGY
    )


@pytest.mark.parametrize(
    ("xp", "level"),
    [(0, 0), (99, 0), (100, 1), (399, 1), (400, 2), (899, 2), (900, 3)],
)
def test_level_boundaries(xp, level):
    assert level_for_xp(xp) == level


def test_level_for_large_value_is_exact():
    assert level_for_xp(100 * 10**12) == 10**6
    assert level_for_xp(100 * 10**12 - 1) == 10**6 - 1


def test_negative_xp_raises():
    with pytest.raises(ValueError):
        level_for_xp(-1)


def test_negative_level_raises():
    with pytest.raises(ValueError):
        xp_for_level(-1)


@pytest.mark.parametrize(
    ("xp", "expected"),
    [
        (0, (0, 0, 100, 100)),
        (99, (0, 99, 100, 1)),
        (100, (1, 0, 300, 300)),
        (250, (1, 150, 300, 150)),
        (400, (2, 0, 500, 500)),
    ],
)
def test_progress_values(xp, expected):
    p = progress_for_xp(xp)
    assert p.total_xp == xp
    assert (p.level, p.xp_into_level, p.xp_for_next_level, p.xp_to_next_level) == expected


def test_local_day_uses_time_zone():
    tz = ZoneInfo("America/Sao_Paulo")
    assert local_day(datetime(2026, 11, 11, 1, 0, tzinfo=UTC), tz) == date(2026, 11, 10)
    assert local_day(datetime(2026, 11, 11, 3, 0, tzinfo=UTC), tz) == date(2026, 11, 11)


def test_local_day_rejects_naive_datetime():
    with pytest.raises(ValueError):
        local_day(datetime(2026, 11, 10, 12, 0), ZoneInfo("UTC"))
