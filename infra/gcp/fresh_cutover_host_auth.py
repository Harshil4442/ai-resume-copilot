"""Use the normal host SDK manager; emit ephemeral credentials to an owned FD.

Never inspect credential-store bodies or transfer a refresh token. No provider
operation is performed at import, and stdout/stderr never carry credentials.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import stat
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SDK = Path("/opt/homebrew/share/google-cloud-sdk/lib")
ACCOUNT = "harshilyogeshbhai886@gmail.com"
PROJECT = "ai-resume-parser-482412"
MIN_TTL_SECONDS = 3570


def fresh_google_credential(store: Any, now: datetime | None = None) -> dict[str, Any]:
    credential = store.LoadFreshCredential(
        account=ACCOUNT, min_expiry_duration="1h", allow_account_impersonation=False,
    )
    token, expiry = credential.token, credential.expiry
    if (type(token) is not str or re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", token) is None
            or not isinstance(expiry, datetime)):
        raise ValueError("cutover_host_auth_shape_refused")
    expiry = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry.astimezone(UTC)
    if expiry.timestamp() - (now or datetime.now(UTC)).timestamp() < MIN_TTL_SECONDS:
        raise ValueError("cutover_host_auth_expiry_refused")
    return {"google_access_token": token, "google_expires_at": expiry.timestamp()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-fd", type=int, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    if args.credential_fd <= 2 or not stat.S_ISFIFO(os.fstat(args.credential_fd).st_mode):
        raise ValueError("cutover_host_auth_fd_refused")
    sys.path[:0] = [str(SDK), str(SDK / "third_party")]
    from googlecloudsdk.core import properties
    from googlecloudsdk.core.credentials import store
    p = properties.VALUES
    if (p.core.account.Get() != ACCOUNT or p.core.project.Get() != PROJECT
            or p.core.universe_domain.Get() != "googleapis.com"
            or not p.core.disable_file_logging.GetBool() or p.core.log_http.GetBool()
            or p.auth.impersonate_service_account.Get() or p.auth.credential_file_override.Get()
            or p.auth.access_token_file.Get() or os.environ.get("CLOUDSDK_AUTH_ACCESS_TOKEN")
            or p.auth.disable_credentials.GetBool() or p.auth.disable_ssl_validation.GetBool()
            or p.proxy.address.Get() or p.proxy.port.Get() or p.proxy.proxy_type.Get()
            or p.auth.token_host.Get() not in (None, "", "https://oauth2.googleapis.com/token")):
        raise ValueError("cutover_host_normal_manager_refused")
    value = fresh_google_credential(store)
    raw = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    value.clear()
    if len(raw) > 16384:
        raise ValueError("cutover_host_auth_pipe_bound")
    while raw:
        written = os.write(args.credential_fd, raw)
        raw = raw[written:]


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        raise SystemExit(2) from None
