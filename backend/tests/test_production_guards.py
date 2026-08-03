import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _database_import(*, app_env: str, database_url: str | None):
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env["APP_ENV"] = app_env
    if database_url is None:
        env.pop("DATABASE_URL", None)
    else:
        env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-c", "import backend.app.database"],
        env=env,
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_production_requires_durable_database_configuration():
    missing = _database_import(app_env="production", database_url=None)
    assert missing.returncode != 0
    assert "DATABASE_URL must be configured" in (missing.stderr + missing.stdout)

    sqlite = _database_import(app_env="production", database_url="sqlite:///./prod.db")
    assert sqlite.returncode != 0
    assert "SQLite is not supported" in (sqlite.stderr + sqlite.stdout)


def test_development_may_use_local_sqlite():
    development = _database_import(app_env="development", database_url="sqlite:///./dev.db")
    assert development.returncode == 0, development.stderr
