"""Actual in-process migration must preserve application operational logging."""
from __future__ import annotations

import logging

from test_employer_admissions_postgres import _migrate
from test_employer_admissions_postgres import pg_engine as pg_engine


def test_actual_migration_preserves_existing_application_loggers(pg_engine, monkeypatch):
    loggers = [logging.getLogger(name) for name in ("catalog_latency", "ai_resume_copilot.auth")]
    for logger in loggers:
        monkeypatch.setattr(logger, "disabled", False)

    _migrate(pg_engine, "head")

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = Capture()
    try:
        for logger in loggers:
            assert logger.disabled is False, f"Migration disabled {logger.name}"
            logger.addHandler(capture)
            logger.warning("Synthetic migration logging probe")
        assert {record.name for record in records} == {logger.name for logger in loggers}
    finally:
        for logger in loggers:
            logger.removeHandler(capture)
