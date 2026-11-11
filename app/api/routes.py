"""JSON API. Every route here delegates to app.services."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.services.progress import get_progress
from app.services.status import get_status

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return asdict(get_status())


@router.get("/progress")
def progress(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """XP, level, today's Energy, the Reserve and the latest wins. Read-only."""
    return asdict(get_progress(session, settings.app_timezone))
