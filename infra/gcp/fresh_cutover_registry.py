"""Expected-state review declaration, never an observation or Fence witness.

Final source, image and review-time inputs must be bound by the reviewer. Native
capture/SQL/provider/queue/backup gates remain mandatory during execution.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

SERVING_MARKER = "b306f135fcd5d8f10336c8cfb4174c71ac028649"
ROLES = {
    "HIREWIZ_CUTOVER_ROLE_RETIRED": "neondb_owner",
    "HIREWIZ_CUTOVER_ROLE_RUNTIME": "hirewiz_runtime_20261010_r1",
    "HIREWIZ_CUTOVER_ROLE_MIGRATION": "hirewiz_migrator_20261010_r1",
    "HIREWIZ_CUTOVER_OPERATOR_OWNER": "hirewiz_source_owner_20261014_r1",
    "HIREWIZ_CUTOVER_OPERATOR_CLOUD": "cloud_admin",
    "HIREWIZ_CUTOVER_OPERATOR_SERVICE": "neon_service",
}


def expected_registry(release: str, image: str, reviewed_at: str) -> tuple[bytes, str]:
    if not re.fullmatch(r"[a-f0-9]{40}", release) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", image
    ):
        raise ValueError("registry_final_source_image_required")
    reviewed = datetime.fromisoformat(reviewed_at)
    if reviewed.tzinfo is None:
        raise ValueError("registry_review_timezone_required")
    value: dict[str, Any] = {
        "format_version": 1,
        "reviewed_at": reviewed_at,
        "scope": "named_project_and_declared_external_consumers",
        "consumer_inventory_complete": True,
        "shared_credentials_resolved": True,
        "unknown_consumer_count": 0,
        "provider_fencing_verified": False,
        "queue_admission_closed": False,
        "protected_backup_verified": False,
        "serving_commit": SERVING_MARKER,
        "candidate_commit": release,
        "candidate_image": image,
        "expected_database_schema": "20261008_0009",
        "candidate_schema": "20261011_0016",
        "expected_system_identifier_sha256": "cabc69e0dc6343b8474c3e4d7fc4ca48e8a93e45c1694d1961a93ee56e4385f0",
        "expected_database_oid": 16391,
        "database_namespace": "public",
        "role_env": {
            "retired": "HIREWIZ_CUTOVER_ROLE_RETIRED",
            "replacement_runtime": "HIREWIZ_CUTOVER_ROLE_RUNTIME",
            "replacement_migration": "HIREWIZ_CUTOVER_ROLE_MIGRATION",
        },
        "retired_role_mode": "deleted",
        "retired_role_oid": 16387,
        "database_operator_role_env": [
            "HIREWIZ_CUTOVER_OPERATOR_OWNER",
            "HIREWIZ_CUTOVER_OPERATOR_CLOUD",
            "HIREWIZ_CUTOVER_OPERATOR_SERVICE",
        ],
        "consumers": [
            {
                "label": label,
                "kind": kind,
                "credential": "retired",
                "release": SERVING_MARKER,
                "state": "fenced",
                "generation_disabled": True,
            }
            for label, kind in (
                ("api", "api"),
                ("analysis", "analysis"),
                ("employer", "employer"),
                ("migration", "migration"),
            )
        ],
    }
    # The true completeness/desired-fenced values are explicitly reviewer intent
    # for the user's dedicated-credential scope, not current native evidence.
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return raw, hashlib.sha256(raw).hexdigest()
