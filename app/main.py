"""Application entry point.

Layering rule (see docs/decisions/0001-python-fastapi-htmx-stack.md):
- app/services: domain logic (events, XP, level). No FastAPI or HTML here.
- app/api: JSON routes under /api that call the service layer.
- app/web: thin HTMX routes that call the service layer and render HTML.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.web.routes import router as web_router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Second Wind", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(api_router)
app.include_router(web_router)
