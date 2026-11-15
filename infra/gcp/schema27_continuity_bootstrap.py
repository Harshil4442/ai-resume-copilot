"""Resolve only the exact continuity bootstrap through the retained Neon session."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

import certifi
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from infra.gcp import schema27_continuity_scope as scope

CLI_HOME = Path("/Users/harshil/.codex/hirewiz-evidence/20261013/neon-normal-manager-metadata-route-private-g1xawwzt")
CLI_ENTRY = CLI_HOME / "cli-install/node_modules/neon/dist/cli.js"
CLI_ENTRY_SHA256 = "5abbfa2e23aa1673154f236f3c5179094d1170825f168ec045ff8d35f62315c6"
CLI_SESSION = CLI_HOME / "credential-session"
NODE = "/opt/homebrew/bin/node"
BOOTSTRAP_OID = 16392
PUBLIC_OID = 2200
ORG = "org-ancient-pond-42861142"


def _need(value, reason):
    scope.rolling.need(value, "schema27_continuity_bootstrap_" + reason)


def migration_identity(database: scope.ContinuityDatabasePin):
    from scripts.browser_schema19_step import MigrationIdentity

    database.validate()
    return MigrationIdentity(
        database.cluster_sha256, database.database_oid, BOOTSTRAP_OID, "public", PUBLIC_OID,
        scope.neon.NAMESPACE_OWNER_OID, scope.neon.DATABASE_OWNER, BOOTSTRAP_OID,
        *database.migrator_role, *database.runtime_role, *database.observer_role,
    )


def _check_manager():
    scope._verify_private_disk()
    for path in (CLI_HOME, CLI_SESSION):
        info = path.lstat()
        _need(path.resolve() == path and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
              and stat.S_IMODE(info.st_mode) == 0o700, "manager_directory")
    info = CLI_ENTRY.lstat()
    _need(CLI_ENTRY.resolve() == CLI_ENTRY and stat.S_ISREG(info.st_mode)
          and not info.st_mode & 0o022 and info.st_uid == os.getuid() and 0 < info.st_size <= 1048576
          and hashlib.sha256(CLI_ENTRY.read_bytes()).hexdigest() == CLI_ENTRY_SHA256, "manager_entry")


def _get(route):
    result = subprocess.run(
        [NODE, str(CLI_ENTRY), "--config-dir", str(CLI_SESSION), "--force-auth=false",
         "api", route, "--method", "GET"],
        stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdout=subprocess.PIPE, timeout=20,
        check=False, env={"HOME": str(Path.home()), "PATH": "/opt/homebrew/bin:/usr/bin:/bin", "CI": "1"},
    )
    _need(result.returncode == 0 and 1 < len(result.stdout) < 131072, "manager_read")
    return scope.rolling.release._json(result.stdout)


def _password(database):
    prefix = "/projects/" + database.project
    try:
        _check_manager()
        project = _get(prefix)["project"]
        _need((project["id"], project["org_id"], project["region_id"], project["pg_version"]) ==
              (database.project, ORG, "aws-us-east-2", 17), "provider_project")
        endpoint = _get(prefix + "/endpoints/" + database.endpoint)["endpoint"]
        _need((endpoint["id"], endpoint["project_id"], endpoint["branch_id"], endpoint["host"], endpoint["type"]) ==
              (database.endpoint, database.project, database.branch, database.host, "read_write"),
              "provider_endpoint")
        query = urlencode({"branch_id": database.branch, "endpoint_id": database.endpoint,
                           "database_name": database.database, "role_name": scope.neon.DATABASE_OWNER,
                           "pooled": "false"})
        response = urlsplit(_get(prefix + "/connection_uri?" + query)["uri"])
        _need(response.scheme in {"postgres", "postgresql"} and response.username == scope.neon.DATABASE_OWNER
              and response.hostname == database.host and response.port in (None, 5432)
              and response.path == "/" + database.database and response.password and not response.fragment,
              "provider_uri")
        return unquote(response.password)
    except scope.rolling.RollingDenied:
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise scope.rolling.RollingDenied("schema27_continuity_bootstrap_provider_unavailable") from None


def engine(database: scope.ContinuityDatabasePin):
    """The inherited binding authenticates TLS, cluster, database and role before SQL."""
    from scripts.database_transfer import BoundDatabase, NativeDatabaseIdentity

    actor = migration_identity(database)
    native = NativeDatabaseIdentity(database.host, 5432, database.database, database.cluster_sha256,
                                    database.database_oid, actor.bootstrap, actor.bootstrap_oid, certifi.where(),
                                    channel_binding="require")
    bound = BoundDatabase(native, _password(database))
    return create_engine("postgresql+psycopg://", creator=bound.connect, poolclass=NullPool,
                         echo=False, hide_parameters=True)
