"""Initialize exact25 metadata for one supplied26 migration transaction.

The runtime URL stays in memory and a scoped environment only. Importing the
application creates an unused Engine; it is forbidden from connecting and is
disposed afterward. Alembic continues to execute on its supplied bootstrap
transaction. This helper grants no migration or database authority.
"""
from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import rolling_release as rolling
from sqlalchemy import event


def _source_path(module) -> Path:
    source = getattr(module, "__file__", None)
    if not isinstance(source, str):
        raise rolling.RollingDenied("schema25_metadata_source_changed")
    return Path(source).resolve()


@contextmanager
def initialized_metadata(root: Path, observer, runtime_reference: str):
    root = root.resolve()
    rolling.need(not any(name in sys.modules for name in (
        "app.database", "backend.app.database", "app.models", "backend.app.models")),
                 "schema25_metadata_fresh_process_required")
    application = sys.modules.get("app")
    package_path = None
    if application is not None:
        rolling.need(_source_path(application).read_bytes()
                     == (root / "backend/app/__init__.py").read_bytes()
                     and isinstance(getattr(application, "__path__", None), list),
                     "schema25_metadata_source_changed")
        package_path = list(application.__path__)
    module = observer._module("backend/scripts/monetary_cutover_preflight.py",
                              "hirewiz_schema25_metadata_runtime")
    neon = module.neon_identity
    _binding, target = neon.collect_schema25_release_target(
        observer, runtime_reference, alias="replacement_runtime",
        database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
        expected_schema="20261028_0025")
    # This is the canonical real runtime URL produced by the fixed native
    # collector, including verified TLS settings; never a bootstrap credential.
    runtime_url = target._url.render_as_string(hide_password=False)
    previous = {name: os.environ.get(name) for name in ("DATABASE_URL", "APP_ENV")}
    path = list(sys.path)
    database = None

    def refuse_runtime_connect(*_args, **_kwargs):
        raise rolling.RollingDenied("schema25_metadata_runtime_connection_refused")

    try:
        os.environ.update(DATABASE_URL=runtime_url, APP_ENV="production")
        sys.path.insert(0, str(root / "backend"))
        if application is not None:
            application.__path__ = [str(root / "backend/app")]
        database = importlib.import_module("app.database")
        rolling.need(_source_path(database) == root / "backend/app/database.py"
                     and database.DATABASE_URL == runtime_url,
                     "schema25_metadata_source_changed")
        event.listen(database.engine, "do_connect", refuse_runtime_connect)
        models = importlib.import_module("app.models")
        rolling.need(_source_path(models) == root / "backend/app/models.py"
                     and models.User.metadata is database.Base.metadata,
                     "schema25_metadata_source_changed")
        yield
    finally:
        try:
            if database is not None:
                # The module remains cached for Alembic metadata. Its unused
                # Engine must stay unable to connect even after this scope.
                database.engine.dispose()
        finally:
            sys.path[:] = path
            if application is not None and package_path is not None:
                application.__path__ = package_path
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
