from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Event, ReserveRelease, Win
from app.services import wins
from app.services.wins import (
    get_energy,
    recent_wins,
    record_commit,
    reserve_balance,
    total_xp,
)
from app.services.xp import (
    COMMIT_ENERGY_COST,
    COMMIT_XP,
    ENERGY_PER_DAY,
    RESERVE_DAILY_RELEASE_XP,
    RESERVE_DEPOSIT_XP,
    RULE_VERSION,
    SECOND_WIND_ENERGY,
    CommitGrant,
)
from tests.conftest import noon

FULL_COMMITS = ENERGY_PER_DAY // COMMIT_ENERGY_COST
SECOND_WIND_COMMITS = SECOND_WIND_ENERGY // COMMIT_ENERGY_COST


def count_rows(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def commit_many(record, day, n):
    return [record(noon(day, i)) for i in range(n)]


# --- Energy ---------------------------------------------------------------------------------


def test_first_ten_commits_grant_xp_and_eleventh_deposits(record, session):
    results = commit_many(record, 0, FULL_COMMITS + 1)
    assert all(r.created and r.xp == COMMIT_XP for r in results[:FULL_COMMITS])
    last = results[-1]
    assert (last.xp, last.reserve_deposit) == (0, RESERVE_DEPOSIT_XP)
    assert total_xp(session) == FULL_COMMITS * COMMIT_XP
    assert reserve_balance(session) == RESERVE_DEPOSIT_XP


def test_next_day_first_commit_releases_reserve_and_gets_full_energy(record, session):
    commit_many(record, 0, FULL_COMMITS + 1)  # reserve 5
    result = record(noon(1))
    assert result.xp == COMMIT_XP
    assert result.reserve_release == RESERVE_DEPOSIT_XP
    assert reserve_balance(session) == 0


def test_release_never_exceeds_balance_and_no_row_when_zero(record, session):
    first = record(noon(0))
    assert first.reserve_release == 0
    assert count_rows(session, ReserveRelease) == 0
    commit_many(record, 1, FULL_COMMITS + 1)  # deposits 5; day 1 first commit releases 0
    assert count_rows(session, ReserveRelease) == 0
    assert reserve_balance(session) == RESERVE_DEPOSIT_XP
    assert record(noon(2)).reserve_release == RESERVE_DEPOSIT_XP
    assert reserve_balance(session) == 0
    assert count_rows(session, ReserveRelease) == 1


def test_large_balance_releases_30_per_day_on_first_commit_only(record, session):
    commit_many(record, 0, FULL_COMMITS + 20)  # 20 deposits -> 100
    assert reserve_balance(session) == 20 * RESERVE_DEPOSIT_XP
    balance = reserve_balance(session)
    for day in (1, 2, 3):
        first = record(noon(day))
        second = record(noon(day, 1))
        assert first.reserve_release == RESERVE_DAILY_RELEASE_XP
        assert second.reserve_release == 0
        balance -= RESERVE_DAILY_RELEASE_XP
        assert reserve_balance(session) == balance


def test_balance_never_negative_over_many_days(record, session):
    for day in range(8):
        for i in range(FULL_COMMITS + (day % 3) * 4):
            record(noon(day, i))
            assert reserve_balance(session) >= 0
    assert reserve_balance(session) >= 0


def test_unused_energy_does_not_roll_over(record, session, tz):
    record(noon(0))  # light day
    commit_many(record, 1, FULL_COMMITS)
    over = record(noon(1, 30))
    assert over.xp == 0
    assert get_energy(session, date(2026, 11, 11), tz).energy_cap == ENERGY_PER_DAY


def test_second_wind_after_three_day_gap_gives_double_cap(record):
    record(noon(0))
    results = commit_many(record, 3, SECOND_WIND_COMMITS + 1)
    assert all(r.xp == COMMIT_XP for r in results[:SECOND_WIND_COMMITS])
    assert results[-1].xp == 0
    assert results[-1].reserve_deposit == RESERVE_DEPOSIT_XP


def test_no_second_wind_after_two_day_gap(record):
    record(noon(0))
    results = commit_many(record, 2, FULL_COMMITS + 1)
    assert all(r.xp == COMMIT_XP for r in results[:FULL_COMMITS])
    assert results[-1].xp == 0


def test_first_day_ever_has_no_second_wind(record):
    results = commit_many(record, 0, FULL_COMMITS + 1)
    assert results[-1].xp == 0


def test_get_energy_on_empty_day_after_gap_shows_second_wind(record, session, tz):
    record(noon(0))
    status = get_energy(session, date(2026, 11, 13), tz)
    assert (status.energy_cap, status.energy_spent) == (SECOND_WIND_ENERGY, 0)
    assert status.energy_left == SECOND_WIND_ENERGY


def test_get_energy_on_empty_first_day_is_full(session, tz):
    status = get_energy(session, date(2026, 11, 10), tz)
    assert (status.energy_cap, status.energy_spent, status.energy_left) == (
        ENERGY_PER_DAY,
        0,
        ENERGY_PER_DAY,
    )


def test_get_energy_without_day_means_today(session, tz):
    before = datetime.now(tz).date()
    status = get_energy(session, None, tz)
    after = datetime.now(tz).date()  # tolerate the run crossing local midnight
    assert status.day in {before, after}
    assert status.energy_left == ENERGY_PER_DAY


def test_get_energy_after_commits_shows_spent_and_left(record, session, tz):
    commit_many(record, 0, 3)
    status = get_energy(session, date(2026, 11, 10), tz)
    assert status.energy_spent == 3 * COMMIT_ENERGY_COST
    assert status.energy_left == ENERGY_PER_DAY - 3 * COMMIT_ENERGY_COST


def test_energy_left_never_negative(record, session, tz):
    commit_many(record, 0, FULL_COMMITS + 5)
    assert get_energy(session, date(2026, 11, 10), tz).energy_left == 0


# --- Duplicates and races -------------------------------------------------------------------


def test_duplicate_sha_grants_nothing(record, session):
    first = record(noon(0), sha="abc")
    assert first.created
    before = (count_rows(session, Event), count_rows(session, Win), total_xp(session))
    again = record(noon(0, 5), sha="abc")
    assert (again.created, again.xp, again.reserve_deposit, again.reserve_release) == (
        False,
        0,
        0,
        0,
    )
    assert (count_rows(session, Event), count_rows(session, Win), total_xp(session)) == before


def test_race_between_check_and_insert_is_a_duplicate(record, session, monkeypatch):
    record(noon(0), sha="abc")
    real = wins._event_exists
    calls = []

    def flaky(sess, sha):
        calls.append(sha)
        return False if len(calls) == 1 else real(sess, sha)

    monkeypatch.setattr(wins, "_event_exists", flaky)
    result = record(noon(0, 1), sha="abc")
    assert result.created is False
    assert (result.xp, result.reserve_deposit, result.reserve_release) == (0, 0, 0)
    assert count_rows(session, Event) == 1
    assert count_rows(session, Win) == 1

    monkeypatch.setattr(wins, "_event_exists", real)
    nxt = record(noon(0, 2), sha="def")
    assert nxt.created and nxt.xp == COMMIT_XP
    assert count_rows(session, Win) == 2


def test_error_while_storing_win_is_raised_and_stores_nothing(record, session, monkeypatch):
    # A grant the database rejects (negative XP breaks a CHECK constraint) must surface as an
    # error, not be mistaken for a duplicate, and must not leave the event behind.
    monkeypatch.setattr(
        wins,
        "grant_for_commit",
        lambda state: CommitGrant(xp=-1, energy_spent=0, reserve_deposit=0, reserve_release=0),
    )
    with pytest.raises(IntegrityError):
        record(noon(0), sha="abc")
    assert count_rows(session, Event) == 0
    assert count_rows(session, Win) == 0

    monkeypatch.undo()
    assert record(noon(0, 1), sha="abc").created  # the session is still usable


def test_empty_sha_rejected(session, tz):
    with pytest.raises(ValueError):
        record_commit(
            session,
            sha="",
            repo="r",
            author_username=None,
            occurred_at=noon(0),
            received_at=noon(0),
            payload={},
            tz=tz,
        )


# --- Time zones -----------------------------------------------------------------------------


def test_received_utc_evening_counts_for_previous_local_day(record, session, tz):
    record(datetime(2026, 11, 11, 1, 0, tzinfo=UTC))  # received 22:00 on Nov 10 in Sao Paulo
    assert session.scalar(select(Win.local_day)) == date(2026, 11, 10)
    record(noon(0))  # also Nov 10 locally
    assert get_energy(session, date(2026, 11, 10), tz).energy_spent == 2 * COMMIT_ENERGY_COST
    assert get_energy(session, date(2026, 11, 11), tz).energy_spent == 0


def test_local_midnight_starts_a_new_energy_bar(record, session, tz):
    commit_many(record, 0, FULL_COMMITS)
    result = record(datetime(2026, 11, 11, 3, 0, tzinfo=UTC))  # received 00:00 Nov 11 locally
    assert result.xp == COMMIT_XP
    assert session.scalars(select(Win.local_day).order_by(Win.id.desc())).first() == date(
        2026, 11, 11
    )


@pytest.mark.parametrize(
    ("occurred_at", "received_at"),
    [
        (datetime(2026, 11, 10, 12, 0), noon(0)),
        (noon(0), datetime(2026, 11, 10, 12, 0)),
    ],
    ids=["naive_occurred_at", "naive_received_at"],
)
def test_naive_datetime_rejected_and_nothing_stored(session, tz, occurred_at, received_at):
    with pytest.raises(ValueError):
        record_commit(
            session,
            sha="abc",
            repo="r",
            author_username=None,
            occurred_at=occurred_at,
            received_at=received_at,
            payload={},
            tz=tz,
        )
    assert count_rows(session, Event) == 0
    assert count_rows(session, Win) == 0


# --- Stored rows and totals -----------------------------------------------------------------


def test_totals_equal_sum_of_stored_rows(record, session):
    commit_many(record, 0, FULL_COMMITS + 6)
    commit_many(record, 1, 3)
    session.flush()
    win_xp = session.scalar(select(func.sum(Win.xp)))
    released = session.scalar(select(func.coalesce(func.sum(ReserveRelease.xp), 0)))
    deposited = session.scalar(select(func.sum(Win.reserve_deposit)))
    assert total_xp(session) == win_xp + released
    assert reserve_balance(session) == deposited - released


def test_totals_are_zero_on_empty_database(session):
    assert total_xp(session) == 0
    assert reserve_balance(session) == 0


def test_win_stores_rule_version_and_event_times_are_aware(record, session):
    committed, received = noon(-2), noon(0)
    record(received, occurred_at=committed)
    win = session.scalar(select(Win))
    event = session.scalar(select(Event))
    assert win.rule_version == RULE_VERSION
    assert event.occurred_at.tzinfo is not None
    assert (event.occurred_at, event.received_at) == (committed, received)
    assert win.earned_at == received
    assert win.local_day == date(2026, 11, 10)  # the received day, not the commit's


# --- recent_wins ----------------------------------------------------------------------------


def test_recent_wins_newest_first_with_limit(record, session):
    for i in range(5):
        record(noon(0, i), sha=f"s{i}", payload={"message": f"m{i}"})
    result = recent_wins(session, limit=3)
    assert [w.sha for w in result] == ["s4", "s3", "s2"]
    assert len(recent_wins(session)) == 5


def test_recent_wins_carries_event_fields(record, session):
    record(noon(0), sha="abc", payload={"message": "hello"})
    (win,) = recent_wins(session)
    assert (win.repo, win.sha, win.message) == ("octo/repo", "abc", "hello")
    assert (win.kind, win.xp, win.local_day) == ("commit", COMMIT_XP, date(2026, 11, 10))


def test_recent_wins_message_none_without_payload_message(record, session):
    record(noon(0), payload={"other": 1})
    assert recent_wins(session)[0].message is None


def test_recent_wins_attaches_release_to_triggering_win(record, session):
    commit_many(record, 0, FULL_COMMITS + 1)  # reserve 5
    record(noon(1), sha="next")
    record(noon(1, 1), sha="later")
    by_sha = {w.sha: w for w in recent_wins(session, limit=50)}
    assert by_sha["next"].reserve_release == RESERVE_DEPOSIT_XP
    assert by_sha["later"].reserve_release == 0
    assert sum(w.reserve_release for w in by_sha.values()) == RESERVE_DEPOSIT_XP


@pytest.mark.parametrize("limit", [0, -1])
def test_recent_wins_rejects_limit_below_one(session, limit):
    with pytest.raises(ValueError):
        recent_wins(session, limit=limit)
