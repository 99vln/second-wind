"""GitHub push webhooks: the signature check, which commits earn XP, and recording them.

No FastAPI here. The route in app/api reads the raw body and headers, and hands them over.
Push payload reference: https://docs.github.com/en/webhooks/webhook-events-and-payloads#push
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.wins import Outcome, record_commit

SIGNATURE_PREFIX = "sha256="

# The committer GitHub records for commits made in its web UI, such as the merge and squash
# buttons. Those commits repeat work already counted on the branch.
WEB_FLOW_COMMITTER = "web-flow"

_HEX_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # SHA-1 or SHA-256 object names


class InvalidPayload(ValueError):
    """The push payload, or a commit that would earn XP, does not have GitHub's shape."""


@dataclass(frozen=True)
class PushCommit:
    """A commit from a push payload that earns XP."""

    sha: str
    repo: str  # "owner/name"
    author_username: str
    timestamp: datetime  # aware, from the commit's ISO 8601 timestamp
    raw: dict[str, Any]  # the commit object exactly as GitHub sent it


@dataclass(frozen=True)
class PushCounts:
    received: int  # commits in the payload
    counted: int  # commits that earn XP: mine, and not made by GitHub's web UI
    new: int  # counted commits recorded for the first time
    duplicates: int  # counted commits already recorded (a redelivery, or another branch)
    too_old: int  # counted commits older than MAX_COMMIT_AGE_DAYS: nothing recorded
    xp_awarded: int  # XP granted by the commits themselves
    reserve_deposited: int  # XP put into the Reserve
    reserve_released: int  # Reserve XP moved into total XP by this push


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check GitHub's X-Hub-Signature-256 header (`sha256=<hex>`) against the raw body.

    A missing header, another prefix, or anything but 64 hex digits is rejected.
    """
    if not secret or not header or not header.startswith(SIGNATURE_PREFIX):
        return False
    received_hex = header.removeprefix(SIGNATURE_PREFIX)
    if not _HEX_SHA256.fullmatch(received_hex):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(expected, bytes.fromhex(received_hex))


def counted_commits(payload: Any, username: str) -> list[PushCommit]:
    """The commits in a push payload that earn XP, in payload order. Every branch counts.

    A commit counts only if its author's GitHub username is `username` (ignoring case) and
    its committer is not GitHub's web UI. Commits without an author username (other people,
    bots, emails not linked to an account) do not count.

    Raises InvalidPayload when the payload is not a push payload, or when a commit that would
    count lacks a valid SHA or a timestamp with an offset. Commits that do not count are not
    inspected further.
    """
    commits = _commit_list(payload)
    wanted = username.lower()
    if not wanted:
        return []

    counted = []
    for commit in commits:
        if not isinstance(commit, dict):
            raise InvalidPayload("each commit must be an object")
        author = _username(commit, "author")
        if author is None or author.lower() != wanted:
            continue
        if _username(commit, "committer") == WEB_FLOW_COMMITTER:
            continue
        counted.append(
            PushCommit(
                sha=_sha(commit),
                repo=_repo(payload),
                author_username=author,
                timestamp=_timestamp(commit),
                raw=commit,
            )
        )
    return counted


def handle_push(
    session: Session,
    payload: Any,
    settings: Settings,
    *,
    received_at: datetime | None = None,
) -> PushCounts:
    """Record the counted commits of a push and commit the transaction.

    Commits are recorded oldest first, all with the same received_at (now, by default), so the
    whole push counts for the local day on which it arrived. Recording is idempotent on the
    SHA: redelivering a push grants nothing new. Total XP gained by the push is
    xp_awarded + reserve_released.
    """
    if received_at is None:
        received_at = datetime.now(UTC)
    received = len(_commit_list(payload))
    commits = sorted(
        counted_commits(payload, settings.github_username or ""), key=lambda c: c.timestamp
    )

    results = [
        record_commit(
            session,
            sha=commit.sha,
            repo=commit.repo,
            author_username=commit.author_username,
            occurred_at=commit.timestamp,
            received_at=received_at,
            payload=commit.raw,
            tz=settings.app_timezone,
        )
        for commit in commits
    ]
    session.commit()

    return PushCounts(
        received=received,
        counted=len(results),
        new=sum(r.outcome is Outcome.NEW for r in results),
        duplicates=sum(r.outcome is Outcome.DUPLICATE for r in results),
        too_old=sum(r.outcome is Outcome.TOO_OLD for r in results),
        xp_awarded=sum(r.xp for r in results),
        reserve_deposited=sum(r.reserve_deposit for r in results),
        reserve_released=sum(r.reserve_release for r in results),
    )


def _commit_list(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        raise InvalidPayload("payload must be an object")
    commits = payload.get("commits", [])  # absent or empty when a branch is deleted
    if not isinstance(commits, list):
        raise InvalidPayload("commits must be a list")
    return commits


def _username(commit: dict[str, Any], role: str) -> str | None:
    person = commit.get(role)
    if not isinstance(person, dict):
        return None
    username = person.get("username")
    return username if isinstance(username, str) and username else None


def _sha(commit: dict[str, Any]) -> str:
    sha = commit.get("id")
    if not isinstance(sha, str) or not _COMMIT_SHA.fullmatch(sha):
        raise InvalidPayload("commit id must be a lowercase hex SHA")
    return sha


def _repo(payload: dict[str, Any]) -> str:
    repository = payload.get("repository")
    name = repository.get("full_name") if isinstance(repository, dict) else None
    if not isinstance(name, str) or not name or len(name) > 255:
        raise InvalidPayload("repository.full_name is required")
    return name


def _timestamp(commit: dict[str, Any]) -> datetime:
    value = commit.get("timestamp")
    if not isinstance(value, str):
        raise InvalidPayload("commit timestamp is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidPayload("commit timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidPayload("commit timestamp must include an offset")
    return parsed
