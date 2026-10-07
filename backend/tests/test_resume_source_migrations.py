from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from backend.app.migrations import run_migrations
from sqlalchemy import create_engine, inspect, text


def test_startup_source_migration_preserves_legacy_resume_and_is_repeatable(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-source.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            connection.execute(
                text("CREATE TABLE resumes (id INTEGER PRIMARY KEY, raw_text TEXT)")
            )
            connection.execute(text("INSERT INTO resumes VALUES (1, 'Original resume content')"))
        run_migrations(engine)
        run_migrations(engine)
        columns = {column["name"]: column for column in inspect(engine).get_columns("resumes")}
        assert columns["source_document"]["nullable"] is True
        assert columns["source_format"]["nullable"] is True
        with engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM resumes WHERE id=1")).mappings().one()
        assert row["raw_text"] == "Original resume content"
        assert row["source_document"] is None
        assert row["source_format"] is None
    finally:
        engine.dispose()


def test_alembic_adds_source_columns_without_rewriting_existing_resumes(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'alembic-source.db'}"
    env = os.environ.copy()
    env.update({"APP_ENV": "test", "DATABASE_URL": database_url})

    def upgrade(target):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", target],
            cwd=backend_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    upgrade("20260803_0003")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO resumes (id, original_filename, raw_text) "
                     "VALUES (1, 'legacy.pdf', 'Original resume content')")
            )
        upgrade("head")
        columns = {column["name"]: column for column in inspect(engine).get_columns("resumes")}
        assert columns["source_document"]["nullable"] is True
        assert columns["source_format"]["nullable"] is True
        with engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM resumes WHERE id=1")).mappings().one()
        assert row["original_filename"] == "legacy.pdf"
        assert row["raw_text"] == "Original resume content"
        assert row["source_document"] is None
        assert row["source_format"] is None
    finally:
        engine.dispose()
