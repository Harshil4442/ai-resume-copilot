"""Ephemeral two-line RAM handoff to the full source cutover entry point.

Credentials enter through owned stdin after the host joins the container. They
are installed only in process memory, never DockerConfig, argv or a file.
"""
from __future__ import annotations

import json
import logging
import os
import re
import runpy
import sys
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

ENTRY = "/opt/hirewiz-release/checkout/infra/gcp/fresh_cutover_execute.py"
MIN_TTL_SECONDS = 3545


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("cutover_auth_duplicate_key")
        result[key] = value
    return result


@contextmanager
def ram_authentication(raw: bytes, *, now: float | None = None) -> Iterator[None]:
    if type(raw) is not bytes or not 0 < len(raw) <= 24576:
        raise ValueError("cutover_auth_pipe_bound")
    value = json.loads(raw, object_pairs_hook=pairs)
    raw = b""
    if (type(value) is not dict or set(value) != {
            "google_access_token", "google_expires_at", "github_token"}
            or any(type(value[key]) is not str or
                re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", value[key]) is None
                for key in ("google_access_token", "github_token"))
            or type(value["google_expires_at"]) not in (int, float)
            or not MIN_TTL_SECONDS <= value["google_expires_at"] - (
                time.time() if now is None else now) <= 3660
            or any(key in os.environ for key in ("CLOUDSDK_AUTH_ACCESS_TOKEN", "GH_TOKEN"))):
        raise ValueError("cutover_auth_shape_or_expiry_refused")
    os.environ["CLOUDSDK_AUTH_ACCESS_TOKEN"] = value["google_access_token"]
    os.environ["GH_TOKEN"] = value["github_token"]
    value.clear()
    try:
        yield
    finally:
        os.environ.pop("CLOUDSDK_AUTH_ACCESS_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)


def main() -> None:
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    try:
        with ram_authentication(sys.stdin.buffer.readline(24578)):
            runpy.run_path(ENTRY, run_name="__main__")
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            raise
        os.write(1, b'{"event":"cutover_stopped_reconcile_only","automatic_replay":false,"goal_complete":false}\n')
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
