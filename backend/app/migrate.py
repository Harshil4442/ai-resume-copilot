from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command

from .database import engine

log = logging.getLogger("hirewiz.migrate")
MIGRATION_LOCK_ID = 721_994_372


def upgrade_database() -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        finally:
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID},
                )
                connection.commit()
    log.info("Database is at the latest Alembic revision")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade_database()
