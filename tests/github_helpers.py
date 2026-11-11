"""Builders for GitHub push payloads and signatures, shared by the webhook tests."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

SECRET = "s3cret-value"  # noqa: S105 (a test value)
USERNAME = "octo"
REPO = "octo/repo"


def sha_for(n: int | str) -> str:
    """A valid 40-char lowercase hex commit id, stable per input."""
    return hashlib.sha1(f"commit-{n}".encode(), usedforsecurity=False).hexdigest()


def make_commit(
    n: int | str = 1,
    *,
    timestamp: datetime | str | None = None,
    message: str | None = "A commit",
    author: str | None = USERNAME,
    committer: str | None = USERNAME,
    sha: str | None = None,
) -> dict[str, Any]:
    """A push-payload commit. Pass author/committer=None to leave the username out."""
    if timestamp is None:
        timestamp = datetime.now(UTC) - timedelta(minutes=5)
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()
    commit: dict[str, Any] = {
        "id": sha or sha_for(n),
        "timestamp": timestamp,
        "author": {"name": "Someone"} if author is None else {"username": author},
        "committer": {"name": "Someone"} if committer is None else {"username": committer},
    }
    if message is not None:
        commit["message"] = message
    return commit


def make_push(commits: list[dict[str, Any]], repo: str = REPO) -> dict[str, Any]:
    return {"repository": {"full_name": repo}, "commits": commits}


def encode(payload: Any) -> bytes:
    return json.dumps(payload).encode()


def sign(body: bytes, secret: str = SECRET) -> str:
    """The X-Hub-Signature-256 header value for `body`."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
