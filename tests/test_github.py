from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Event, Win
from app.services.github import (
    InvalidPayload,
    counted_commits,
    handle_push,
    verify_signature,
)
from app.services.progress import RECENT_WINS_LIMIT, get_progress
from app.services.xp import MAX_COMMIT_AGE_DAYS, is_second_wind_day, is_too_old
from tests.conftest import noon
from tests.github_helpers import (
    REPO,
    SECRET,
    USERNAME,
    make_commit,
    make_push,
    sha_for,
    sign,
)

BODY = b'{"commits": []}'


@pytest.fixture
def settings(tz) -> Settings:
    return Settings(
        database_url="sqlite://",
        github_webhook_secret=SECRET,
        github_username=USERNAME,
        app_timezone=tz,
    )


# --- verify_signature ---------------------------------------------------------------------


def test_valid_signature_is_accepted() -> None:
    assert verify_signature(SECRET, BODY, sign(BODY))


def test_uppercase_hex_is_accepted() -> None:
    header = "sha256=" + sign(BODY).removeprefix("sha256=").upper()
    assert verify_signature(SECRET, BODY, header)


def test_wrong_secret_is_rejected() -> None:
    assert not verify_signature("other", BODY, sign(BODY))


def test_tampered_body_is_rejected() -> None:
    assert not verify_signature(SECRET, BODY + b" ", sign(BODY))


@pytest.mark.parametrize(
    "header",
    [None, "", "sha1=" + "a" * 40, "sha256=" + "g" * 64, "sha256=abcd", "sha256=" + "a" * 66],
)
def test_malformed_header_is_rejected(header: str | None) -> None:
    assert not verify_signature(SECRET, BODY, header)


def test_signature_without_prefix_is_rejected() -> None:
    assert not verify_signature(SECRET, BODY, sign(BODY).removeprefix("sha256="))


def test_empty_secret_is_rejected_even_with_matching_signature() -> None:
    assert not verify_signature("", BODY, sign(BODY, ""))


# --- counted_commits ----------------------------------------------------------------------


def test_author_filter_ignores_case_and_skips_missing_usernames() -> None:
    payload = make_push(
        [
            make_commit(1, author="OCTO"),
            make_commit(2, author="someone-else"),
            make_commit(3, author=None),
            {**make_commit(4), "author": None},
            {k: v for k, v in make_commit(5).items() if k != "author"},
        ]
    )
    commits = counted_commits(payload, "Octo")
    assert [c.sha for c in commits] == [sha_for(1)]
    assert commits[0].author_username == "OCTO"


def test_web_flow_commits_are_skipped() -> None:
    payload = make_push([make_commit(1, committer="web-flow"), make_commit(2)])
    assert [c.sha for c in counted_commits(payload, USERNAME)] == [sha_for(2)]


def test_commit_fields_are_parsed() -> None:
    commit = make_commit(1, timestamp="2026-11-10T09:30:00-03:00")
    (found,) = counted_commits(make_push([commit], repo="octo/other"), USERNAME)

    assert found.repo == "octo/other"
    assert found.timestamp == datetime(2026, 11, 10, 12, 30, tzinfo=UTC)
    assert found.timestamp.utcoffset() == timedelta(hours=-3)
    assert found.raw == commit


def test_empty_username_counts_nothing() -> None:
    assert counted_commits(make_push([make_commit(1)]), "") == []


def test_no_commits_key_counts_nothing() -> None:
    assert counted_commits({"repository": {"full_name": REPO}}, USERNAME) == []


@pytest.mark.parametrize(
    "commit",
    [
        make_commit(1, sha="abc"),
        make_commit(1, sha=sha_for(1).upper()),
        make_commit(1, timestamp="2026-11-10T09:30:00"),  # naive
        make_commit(1, timestamp="yesterday"),
        {**make_commit(1), "timestamp": None},
    ],
)
def test_counted_commit_with_bad_fields_is_invalid(commit: dict) -> None:
    with pytest.raises(InvalidPayload):
        counted_commits(make_push([commit]), USERNAME)


@pytest.mark.parametrize("repository", [None, {}, {"full_name": ""}, {"full_name": "x" * 256}])
def test_counted_commit_needs_a_repository(repository: object) -> None:
    payload = {"repository": repository, "commits": [make_commit(1)]}
    with pytest.raises(InvalidPayload):
        counted_commits(payload, USERNAME)


def test_commits_that_do_not_count_are_not_validated() -> None:
    bad = make_commit(1, author="dependabot[bot]", sha="nope", timestamp="garbage")
    payload = {"commits": [bad]}  # not even a repository
    assert counted_commits(payload, USERNAME) == []


def test_non_object_commit_is_invalid() -> None:
    with pytest.raises(InvalidPayload):
        counted_commits(make_push(["abc"]), USERNAME)


# --- handle_push --------------------------------------------------------------------------


def test_commits_are_recorded_oldest_first_whatever_the_payload_order(
    session: Session, settings: Settings
) -> None:
    received = noon()
    # 11 commits listed newest first: the 11th recorded (the newest) goes to the Reserve.
    commits = [make_commit(i, timestamp=received - timedelta(minutes=i + 1)) for i in range(11)]

    counts = handle_push(session, make_push(commits), settings, received_at=received)

    assert (counts.xp_awarded, counts.reserve_deposited) == (100, 5)
    newest = commits[0]["id"]
    deposit_sha = session.scalar(
        select(Event.external_id).join(Win, Win.event_id == Event.id).where(Win.xp == 0)
    )
    assert deposit_sha == newest


def test_counts_add_up(session: Session, settings: Settings) -> None:
    received = noon()
    payload = make_push(
        [
            make_commit(1, timestamp=received - timedelta(minutes=1)),
            make_commit(2, timestamp=received - timedelta(minutes=2)),
            make_commit(3, timestamp=received - timedelta(days=9)),
            make_commit(4, author="bot"),
        ]
    )
    handle_push(session, make_push(payload["commits"][:1]), settings, received_at=received)

    counts = handle_push(session, payload, settings, received_at=received)

    assert counts.received == 4
    assert counts.counted == 3
    assert (counts.new, counts.duplicates, counts.too_old) == (1, 1, 1)
    assert counts.new + counts.duplicates + counts.too_old == counts.counted


def test_commit_exactly_seven_days_old_counts(session: Session, settings: Settings) -> None:
    received = noon()
    stamp = received - timedelta(days=MAX_COMMIT_AGE_DAYS)
    counts = handle_push(
        session, make_push([make_commit(1, timestamp=stamp)]), settings, received_at=received
    )
    assert (counts.new, counts.too_old) == (1, 0)


def test_commit_one_second_over_seven_days_is_too_old(session: Session, settings: Settings) -> None:
    received = noon()
    stamp = received - timedelta(days=MAX_COMMIT_AGE_DAYS, seconds=1)
    counts = handle_push(
        session, make_push([make_commit(1, timestamp=stamp)]), settings, received_at=received
    )
    assert (counts.new, counts.too_old) == (0, 1)


def test_received_time_decides_the_local_day(session: Session, settings: Settings) -> None:
    late = datetime(2026, 11, 11, 2, 59, tzinfo=UTC)  # 23:59 on the 10th in Sao Paulo
    early = late + timedelta(minutes=1)  # 00:00 on the 11th
    stamp = late - timedelta(minutes=5)

    handle_push(session, make_push([make_commit(1, timestamp=stamp)]), settings, received_at=late)
    handle_push(session, make_push([make_commit(2, timestamp=stamp)]), settings, received_at=early)

    days = session.scalars(select(Win.local_day).order_by(Win.id)).all()
    assert days == [date(2026, 11, 10), date(2026, 11, 11)]


def test_reserve_release_is_reported_on_the_first_push_of_a_later_day(
    session: Session, settings: Settings
) -> None:
    day0 = noon()
    full_day = [make_commit(i, timestamp=day0 - timedelta(minutes=i + 1)) for i in range(12)]
    first = handle_push(session, make_push(full_day), settings, received_at=day0)
    assert first.reserve_deposited == 10
    assert first.reserve_released == 0

    day1 = noon(1)
    next_push = make_push([make_commit(50, timestamp=day1 - timedelta(minutes=1))])
    second = handle_push(session, next_push, settings, received_at=day1)

    assert second.reserve_released == 10
    assert second.xp_awarded == 10


def test_push_with_unset_username_counts_nothing(session: Session, tz) -> None:
    settings = Settings("sqlite://", SECRET, None, tz)
    counts = handle_push(session, make_push([make_commit(1)]), settings, received_at=noon())
    assert (counts.received, counts.counted) == (1, 0)


def test_invalid_payload_records_nothing(session: Session, settings: Settings) -> None:
    payload = make_push([make_commit(1), make_commit(2, sha="bad")])
    with pytest.raises(InvalidPayload):
        handle_push(session, payload, settings, received_at=noon())
    assert session.scalar(select(Event.id)) is None


# --- xp helpers ---------------------------------------------------------------------------


def test_is_too_old_boundaries() -> None:
    received = noon()
    limit = timedelta(days=MAX_COMMIT_AGE_DAYS)
    assert not is_too_old(received - limit, received)
    assert is_too_old(received - limit - timedelta(microseconds=1), received)
    assert not is_too_old(received + timedelta(hours=1), received)  # clock skew: from the future


def test_is_too_old_compares_across_offsets() -> None:
    received = noon()
    stamp = (received - timedelta(days=7)).astimezone(timezone(timedelta(hours=9)))
    assert not is_too_old(stamp, received)


@pytest.mark.parametrize("args", [(datetime(2026, 1, 1), noon()), (noon(), datetime(2026, 1, 1))])
def test_is_too_old_rejects_naive_datetimes(args: tuple[datetime, datetime]) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_too_old(*args)


def test_second_wind_day_needs_a_gap_of_three_days() -> None:
    today = date(2026, 11, 10)
    assert not is_second_wind_day(today, None)
    assert not is_second_wind_day(today, today - timedelta(days=1))
    assert not is_second_wind_day(today, today - timedelta(days=2))
    assert is_second_wind_day(today, today - timedelta(days=3))
    assert is_second_wind_day(today, today - timedelta(days=30))


# --- get_progress -------------------------------------------------------------------------


def test_progress_shows_second_wind_after_a_three_day_gap(
    session: Session, settings: Settings, tz
) -> None:
    handle_push(session, make_push([make_commit(1)]), settings, received_at=noon())

    for gap, expected in [(2, False), (3, True)]:
        progress = get_progress(session, tz, today=date(2026, 11, 10) + timedelta(days=gap))
        assert progress.energy.second_wind is expected
        assert progress.energy.cap == (200 if expected else 100)
        assert progress.energy.spent == 0


def test_recent_wins_are_capped_and_newest_first(session: Session, settings: Settings, tz) -> None:
    for day in range(RECENT_WINS_LIMIT + 2):
        stamp = noon(day)
        handle_push(
            session,
            make_push([make_commit(day, timestamp=stamp, message=f"day {day}")]),
            settings,
            received_at=stamp,
        )

    wins = get_progress(session, tz, today=date(2026, 12, 1)).recent_wins

    assert len(wins) == RECENT_WINS_LIMIT
    assert wins[0].message == "day 11"
    assert wins[0].sha == sha_for(11)[:7]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("first\nsecond\nthird", "first"),
        ("  padded  \nbody", "padded"),
        ("\n\nleading blank lines\nbody", "leading blank lines"),
        ("", ""),
        ("   \n  ", ""),
        (None, ""),
        ("a" * 100, "a" * 100),
        ("a" * 101, "a" * 99 + "…"),
        ("a" * 98 + " " + "b" * 20, "a" * 98 + "…"),  # no space before the ellipsis
    ],
)
def test_message_shows_only_its_first_line(
    session: Session, settings: Settings, tz, message: str | None, expected: str
) -> None:
    commit = make_commit(1, timestamp=noon(), message=message)
    handle_push(session, make_push([commit]), settings, received_at=noon())

    (win,) = get_progress(session, tz, today=date(2026, 11, 10)).recent_wins

    assert win.message == expected
