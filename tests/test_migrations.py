from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from alembic import command
from app.db import create_db_engine
from app.models import Base

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"


def test_migration_matches_models_and_downgrades_cleanly(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))

    command.upgrade(cfg, "head")
    engine = create_db_engine(url)
    try:
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        command.downgrade(cfg, "base")
        remaining = set(inspect(engine).get_table_names()) & set(Base.metadata.tables)
        assert remaining == set()
    finally:
        engine.dispose()
