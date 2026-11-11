"""JSON API. Every route here delegates to app.services."""

from dataclasses import asdict

from fastapi import APIRouter

from app.services.status import get_status

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return asdict(get_status())
