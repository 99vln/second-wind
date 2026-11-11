import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.webhooks import MAX_BODY_BYTES
from app.db import get_session
from app.main import app
from app.models import Event, Win
from tests.github_helpers import SECRET, USERNAME, encode, make_commit, make_push, sha_for, sign

URL = "/api/webhooks/github"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("GITHUB_USERNAME", USERNAME)
    monkeypatch.setenv("APP_TIMEZONE", "America/Sao_Paulo")
    app.dependency_overrides[get_session] = lambda: (yield session)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def deliver(
    client: TestClient,
    payload: object = None,
    *,
    body: bytes | None = None,
    event: str = "push",
    signature: str | None = "valid",
    delivery: str = "delivery-1",
):
    """POST a delivery. signature="valid" signs the body; None omits the header."""
    if body is None:
        body = encode(payload)
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }
    if signature == "valid":
        headers["X-Hub-Signature-256"] = sign(body)
    elif signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post(URL, content=body, headers=headers)


def row_counts(session: Session) -> tuple[int, int]:
    events = session.scalar(select(func.count(Event.id)))
    wins = session.scalar(select(func.count(Win.id)))
    return events, wins


def push_of(*commits: dict) -> dict:
    return make_push(list(commits))


# --- happy path ---------------------------------------------------------------------------


def test_valid_push_grants_xp_and_spends_energy(client: TestClient) -> None:
    response = deliver(client, push_of(*(make_commit(i) for i in range(3))))

    assert response.status_code == 200
    assert response.json() == {
        "received": 3,
        "counted": 3,
        "new": 3,
        "duplicates": 0,
        "too_old": 0,
        "xp_awarded": 30,
        "reserve_deposited": 0,
        "reserve_released": 0,
    }
    progress = client.get("/api/progress").json()
    assert progress["total_xp"] == 30
    assert progress["energy"] == {
        "day": progress["energy"]["day"],
        "cap": 100,
        "spent": 30,
        "left": 70,
        "second_wind": False,
    }


def test_same_delivery_twice_grants_xp_once(client: TestClient, session: Session) -> None:
    payload = push_of(make_commit(1), make_commit(2))
    deliver(client, payload)

    again = deliver(client, payload)

    assert again.status_code == 200
    body = again.json()
    assert (body["new"], body["duplicates"], body["xp_awarded"]) == (0, 2, 0)
    assert client.get("/api/progress").json()["total_xp"] == 20
    assert row_counts(session) == (2, 2)


def test_twelve_commits_in_one_day_give_100_xp_and_10_reserve(client: TestClient) -> None:
    now = datetime.now(UTC)
    commits = [make_commit(i, timestamp=now - timedelta(minutes=30 - i)) for i in range(12)]

    body = deliver(client, push_of(*commits)).json()

    assert body["xp_awarded"] == 100
    assert body["reserve_deposited"] == 10
    progress = client.get("/api/progress").json()
    assert progress["total_xp"] == 100
    assert progress["reserve_balance"] == 10
    assert progress["energy"]["left"] == 0


# --- signature ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "",
        "wrong",
        "sha256=" + "0" * 64,  # right shape, wrong value
        "0" * 64,  # no sha256= prefix
        "sha1=" + "0" * 40,
        "sha256=" + "zz" * 32,  # not hex
        "sha256=abcd",  # wrong length
    ],
)
def test_bad_signature_is_unauthorized_and_records_nothing(
    client: TestClient, session: Session, signature: str | None
) -> None:
    response = deliver(client, push_of(make_commit(1)), signature=signature)

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}  # does not say why
    assert row_counts(session) == (0, 0)


def test_signature_made_with_another_secret_is_unauthorized(client: TestClient) -> None:
    body = encode(push_of(make_commit(1)))
    response = deliver(client, body=body, signature=sign(body, "other-secret"))
    assert response.status_code == 401


def test_signature_of_a_different_body_is_unauthorized(client: TestClient) -> None:
    signature = sign(encode(push_of(make_commit(1))))
    response = deliver(client, push_of(make_commit(2)), signature=signature)
    assert response.status_code == 401


# --- configuration ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GITHUB_WEBHOOK_SECRET", None),
        ("GITHUB_WEBHOOK_SECRET", "   "),
        ("GITHUB_USERNAME", None),
        ("GITHUB_USERNAME", "  "),
    ],
)
def test_missing_configuration_is_503_and_records_nothing(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    response = deliver(client, push_of(make_commit(1)))  # even with a valid signature

    assert response.status_code == 503
    assert row_counts(session) == (0, 0)


# --- event types --------------------------------------------------------------------------


def test_ping_is_ok(client: TestClient) -> None:
    response = deliver(client, {"zen": "Keep it logically awesome."}, event="ping")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_other_events_are_ignored(client: TestClient, session: Session) -> None:
    response = deliver(client, push_of(make_commit(1)), event="issues")
    assert response.status_code == 202
    assert response.json() == {"ignored": "issues"}
    assert row_counts(session) == (0, 0)


def test_unsigned_ping_is_still_unauthorized(client: TestClient) -> None:
    assert deliver(client, {}, event="ping", signature=None).status_code == 401


# --- body size and shape ------------------------------------------------------------------


def test_oversized_body_is_413(client: TestClient) -> None:
    body = b"x" * (MAX_BODY_BYTES + 1)
    assert deliver(client, body=body, signature=sign(body)).status_code == 413


def test_oversized_body_is_refused_before_the_signature_check(client: TestClient) -> None:
    body = b"x" * (MAX_BODY_BYTES + 1)
    assert deliver(client, body=body, signature=None).status_code == 413


def test_body_of_exactly_the_limit_is_not_refused_for_size(client: TestClient) -> None:
    body = b" " * MAX_BODY_BYTES
    response = deliver(client, body=body, event="issues", signature=sign(body))
    assert response.status_code == 202


def test_invalid_json_with_valid_signature_is_400(client: TestClient) -> None:
    assert deliver(client, body=b"{not json").status_code == 400


@pytest.mark.parametrize(
    "payload",
    [[], "text", 5, {"repository": {"full_name": "a/b"}, "commits": "nope"}],
)
def test_json_that_is_not_a_push_payload_is_400(client: TestClient, payload: object) -> None:
    assert deliver(client, payload).status_code == 400


def test_counted_commit_with_bad_sha_is_400_and_nothing_is_recorded(
    client: TestClient, session: Session
) -> None:
    payload = push_of(make_commit(1), make_commit(2, sha="NOT-A-SHA"))
    assert deliver(client, payload).status_code == 400
    assert row_counts(session) == (0, 0)


def test_push_without_commits_is_ok(client: TestClient) -> None:
    body = deliver(client, {"repository": {"full_name": "a/b"}}).json()
    assert body["received"] == 0
    assert body["xp_awarded"] == 0


# --- which commits count ------------------------------------------------------------------


def test_commits_by_others_or_without_username_earn_nothing(
    client: TestClient, session: Session
) -> None:
    payload = push_of(
        make_commit(1, author="dependabot[bot]"),
        make_commit(2, author=None),
        make_commit(3),
    )

    body = deliver(client, payload).json()

    assert (body["received"], body["counted"], body["new"]) == (3, 1, 1)
    assert body["xp_awarded"] == 10
    assert row_counts(session) == (1, 1)


def test_web_flow_committer_earns_nothing(client: TestClient) -> None:
    body = deliver(client, push_of(make_commit(1, committer="web-flow"))).json()
    assert (body["received"], body["counted"], body["xp_awarded"]) == (1, 0, 0)


def test_username_match_ignores_case(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_USERNAME", "Octo")
    body = deliver(client, push_of(make_commit(1, author="octo"))).json()
    assert body["counted"] == 1


# --- received-day rule and age limit ------------------------------------------------------


def test_commit_from_three_days_ago_counts_for_today(client: TestClient, session: Session) -> None:
    old = datetime.now(UTC) - timedelta(days=3)

    body = deliver(client, push_of(make_commit(1, timestamp=old))).json()

    assert (body["new"], body["xp_awarded"]) == (1, 10)
    today = datetime.now(UTC).astimezone(SAO_PAULO).date()
    assert session.scalar(select(Win.local_day)) == today
    assert client.get("/api/progress").json()["energy"]["spent"] == 10


def test_commit_older_than_seven_days_is_ignored(client: TestClient, session: Session) -> None:
    old = datetime.now(UTC) - timedelta(days=8)

    body = deliver(client, push_of(make_commit(1, timestamp=old))).json()

    assert (body["counted"], body["too_old"], body["new"], body["xp_awarded"]) == (1, 1, 0, 0)
    assert row_counts(session) == (0, 0)


# --- progress -----------------------------------------------------------------------------


def test_progress_on_an_empty_database(client: TestClient) -> None:
    progress = client.get("/api/progress").json()

    assert progress["total_xp"] == 0
    assert progress["level"] == 0
    assert (progress["xp_into_level"], progress["xp_for_next_level"]) == (0, 100)
    assert progress["xp_to_next_level"] == 100
    assert progress["energy"]["cap"] == 100
    assert progress["energy"]["spent"] == 0
    assert progress["energy"]["left"] == 100
    assert progress["energy"]["second_wind"] is False
    assert progress["reserve_balance"] == 0
    assert progress["recent_wins"] == []


def test_progress_reports_level_and_recent_wins(client: TestClient) -> None:
    now = datetime.now(UTC)
    commits = [
        make_commit(i, timestamp=now - timedelta(minutes=30 - i), message=f"win {i}\n\nlong body")
        for i in range(12)
    ]
    deliver(client, push_of(*commits))

    progress = client.get("/api/progress").json()

    assert progress["level"] == 1
    assert progress["xp_into_level"] == 0
    assert progress["xp_for_next_level"] == 300
    assert progress["xp_to_next_level"] == 300
    wins = progress["recent_wins"]
    assert len(wins) == 10
    assert [w["message"] for w in wins[:2]] == ["win 11", "win 10"]  # newest first
    newest = wins[0]
    assert newest["sha"] == sha_for(11)[:7]
    assert newest["repo"] == "octo/repo"
    assert (newest["xp"], newest["reserve_deposit"]) == (0, 5)
    assert wins[-1]["xp"] == 10
    assert "earned_at" in newest


# --- logging ------------------------------------------------------------------------------


def test_push_logs_the_delivery_id_but_no_secrets_or_content(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    payload = push_of(make_commit(1, message="top secret message"))
    body = encode(payload)
    signature = sign(body)

    with caplog.at_level(logging.DEBUG):
        deliver(client, body=body, signature=signature, delivery="delivery-xyz")

    logged = caplog.text
    assert "delivery-xyz" in logged
    assert SECRET not in logged
    assert signature not in logged
    assert signature.removeprefix("sha256=") not in logged
    assert "top secret message" not in logged
