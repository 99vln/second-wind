"""GitHub webhook endpoint. HTTP concerns only (size, signature, event type, status codes);
what a push is worth is decided in app.services.github.

Never log the body, the signature header or any secret.
"""

import json
import logging
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.services.github import InvalidPayload, handle_push, verify_signature

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1024 * 1024  # 1 MB
_MAX_HEADER_ECHO = 64  # longest event name or delivery id echoed back or logged

router = APIRouter(prefix="/api/webhooks")


@router.post("/github")
async def github_webhook(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    delivery = request.headers.get("x-github-delivery", "")[:_MAX_HEADER_ECHO]

    # Fail closed: without a secret nothing can be verified, and without a username nothing
    # can count. GitHub shows the 503, and a redelivery works once the setting is fixed.
    if not settings.github_webhook_secret or not settings.github_username:
        logger.error("GitHub webhook is not configured; delivery %r refused", delivery)
        return _json(503, {"detail": "Webhook not configured"})

    body = await _read_body(request, MAX_BODY_BYTES)
    if body is None:
        logger.warning("GitHub delivery %r refused: body over %d bytes", delivery, MAX_BODY_BYTES)
        return _json(413, {"detail": "Payload too large"})

    signature = request.headers.get("x-hub-signature-256")
    if not verify_signature(settings.github_webhook_secret, body, signature):
        logger.warning("GitHub delivery %r refused: signature check failed", delivery)
        return _json(401, {"detail": "Unauthorized"})

    event = request.headers.get("x-github-event", "")[:_MAX_HEADER_ECHO]
    if event == "ping":
        logger.info("GitHub delivery %r: ping", delivery)
        return _json(200, {"ok": True})
    if event != "push":
        logger.info("GitHub delivery %r: ignored event %r", delivery, event)
        return _json(202, {"ignored": event})

    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        logger.warning("GitHub delivery %r refused: body is not valid JSON", delivery)
        return _json(400, {"detail": "Invalid JSON"})

    try:
        # The ledger uses a blocking database session; keep it off the event loop.
        counts = await run_in_threadpool(handle_push, session, payload, settings)
    except InvalidPayload:
        logger.warning("GitHub delivery %r refused: not a valid push payload", delivery)
        return _json(400, {"detail": "Invalid push payload"})

    logger.info("GitHub delivery %r: push %s", delivery, counts)
    return _json(200, asdict(counts))


async def _read_body(request: Request, limit: int) -> bytes | None:
    """The raw body, or None if it is larger than `limit` bytes.

    Reads in chunks and stops early, so an oversized body is never held in memory whole,
    even when it is sent without a Content-Length.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            return None
    return bytes(body)


def _json(status_code: int, content: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=content)
