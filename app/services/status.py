"""Service-layer example: the app's status, independent of transport (JSON or HTML)."""

from dataclasses import dataclass

APP_NAME = "Second Wind"


@dataclass(frozen=True)
class Status:
    app: str
    ok: bool


def get_status() -> Status:
    return Status(app=APP_NAME, ok=True)
