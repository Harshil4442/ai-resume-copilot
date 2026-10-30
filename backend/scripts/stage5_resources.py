"""Only fresh invocation-owned loopback dependencies; no caller database/Redis URI."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import IO
from uuid import uuid4

from scripts.run_cold_browser_journey import _owned_status, _spawn_owned, _stop

EMULATOR = ("gcr.io/google.com/cloudsdktool/google-cloud-cli:573.0.0-emulators@sha256:"
            "1b083a6a9647024a98d4f38c2b51906a0f61ca36adcba11672b8933544962efe")
EMULATOR_REPO_DIGEST = ("gcr.io/google.com/cloudsdktool/google-cloud-cli@sha256:"
                        "1b083a6a9647024a98d4f38c2b51906a0f61ca36adcba11672b8933544962efe")
FORBIDDEN_PORTS = frozenset({55433, 55434})


def pinned_emulator_configuration(value) -> str:
    # OCI manifest/repository digest is not the platform's image configuration
    # ID. Bind the former exactly, then retain the observed latter for this run.
    if (type(value) is not list or len(value) != 1 or type(value[0]) is not dict
            or EMULATOR_REPO_DIGEST not in value[0].get("RepoDigests", [])
            or re.fullmatch(r"sha256:[a-f0-9]{64}", value[0].get("Id", "")) is None):
        raise RuntimeError("Exact already-cached emulator image is required; never pull")
    return value[0]["Id"]


def require_owned_internal_network(value, *, identity: str, nonce: str) -> None:
    if (type(value) is not list or len(value) != 1 or value[0].get("Id") != identity
            or value[0].get("Internal") is not True or value[0].get("Containers") != {}
            or value[0].get("Labels", {}).get("hirewiz.stage5.owner") != nonce):
        raise RuntimeError("Actual owned internal emulator network differs")


def unused_port() -> int:
    for _ in range(20):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        if port not in FORBIDDEN_PORTS:
            return port
    raise RuntimeError("No eligible owned port")


def private_json(path: Path, data) -> None:
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(data, stream, indent=2)
        stream.write("\n")


class Resources:
    def __init__(self, directory: Path, pg_bin: Path, redis_binary: Path):
        if directory.exists():
            raise ValueError("Evidence directory must be new")
        directory.mkdir(mode=0o700, parents=True)
        self.directory, self.pg_bin, self.redis_binary = directory, pg_bin.resolve(), redis_binary.resolve()
        self.nonce = uuid4().hex
        self.name = "hirewiz-stage5-" + self.nonce
        self.processes: list[subprocess.Popen[bytes]] = []
        self.logs: list[IO[bytes]] = []
        self.cleanup: list[dict[str, object]] = []
        self.container_id: str | None = None
        self.network_id: str | None = None
        self.pg_data: Path | None = None
        self.pg_system_identifier: str | None = None
        self.pg_port, self.emulator_port, self.api_port = unused_port(), unused_port(), unused_port()
        if len({self.pg_port, self.emulator_port, self.api_port}) != 3:
            raise RuntimeError("Owned port collision")
        # No inherited SDK credentials, proxies, application configuration or shell profile.
        self.env = {k: v for k, v in os.environ.items() if k in {"PATH", "TMPDIR", "LANG"}}
        self.env["HOME"] = str(directory)
        private_json(directory / "ownership.json", {"nonce": self.nonce, "docker_name": self.name,
            "pg_port": self.pg_port, "emulator_port": self.emulator_port, "api_port": self.api_port,
            "uid": os.getuid()})

    def command(self, command: list[str], *, seconds=30):
        result = subprocess.run(command, env=self.env, cwd=self.directory,
            capture_output=True, timeout=seconds, check=False)
        if result.returncode:
            raise RuntimeError("Owned dependency command failed")
        return result.stdout.decode()

    def spawn(self, command: list[str], label: str):
        log = (self.directory / (label + ".log")).open("xb")
        (self.directory / (label + ".log")).chmod(0o600)
        self.logs.append(log)
        process = _spawn_owned(command, cwd=self.directory, env=self.env, stdout=log, stderr=log)
        self.processes.append(process)
        return process

    def start_postgres(self):
        for name in ("initdb", "postgres", "createdb", "pg_isready", "pg_controldata"):
            if not (self.pg_bin / name).is_file():
                raise RuntimeError("PostgreSQL 17 binaries are required")
        if not self.command([str(self.pg_bin / "postgres"), "--version"]).startswith("postgres (PostgreSQL) 17."):
            raise RuntimeError("PostgreSQL 17 is required")
        data = self.directory / "pg-data"
        self.pg_data = data
        self.command([str(self.pg_bin / "initdb"), "-D", str(data), "-U", "stage5_owned",
                      "--auth-local=trust", "--auth-host=trust", "--no-locale", "--encoding=UTF8"])
        control = self.command([str(self.pg_bin / "pg_controldata"), "-D", str(data)])
        fields = dict(line.split(":", 1) for line in control.splitlines() if ":" in line)
        original_id = fields["Database system identifier"].strip()
        if (re.fullmatch(r"[0-9]{1,20}", original_id) is None
                or fields["Database cluster state"].strip() != "shut down"):
            raise RuntimeError("Fresh owned PostgreSQL creation identity unavailable")
        self.pg_system_identifier = original_id
        private_json(self.directory / "pg-creation-identity.json",
                     {"nonce": self.nonce, "system_identifier": original_id})
        process = self.spawn([str(self.pg_bin / "postgres"), "-D", str(data), "-h", "127.0.0.1",
            "-p", str(self.pg_port), "-k", "", "-c", "max_connections=25",
            "-c", "log_statement=none"], "postgres")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _owned_status(process) is not None:
                raise RuntimeError("Owned PostgreSQL exited")
            try:
                self.command([str(self.pg_bin / "pg_isready"), "-h", "127.0.0.1", "-p",
                    str(self.pg_port), "-U", "stage5_owned", "-d", "postgres"], seconds=2)
                break
            except RuntimeError:
                time.sleep(.1)
        else:
            raise RuntimeError("Owned PostgreSQL readiness expired")
        # Creation is sent once only after a read-only readiness check. Unknown
        # acknowledgement fails the run; it does not become another createdb.
        self.command([str(self.pg_bin / "createdb"), "-h", "127.0.0.1", "-p",
            str(self.pg_port), "-U", "stage5_owned", "stage5_owned"], seconds=5)
        self.dsn = (f"postgresql+psycopg://stage5_owned@127.0.0.1:{self.pg_port}/stage5_owned"
                    "?options=-csearch_path%3Dstage5_owned%20-cstatement_timeout%3D5000")

    def start_emulator(self):
        value = json.loads(self.command(["docker", "image", "inspect", EMULATOR]))
        configuration_id = pinned_emulator_configuration(value)
        self.network_id = self.command(["docker", "network", "create", "--internal", "--label",
                                       "hirewiz.stage5.owner=" + self.nonce, self.name]).strip()
        assert self.network_id is not None
        require_owned_internal_network(json.loads(self.command(["docker", "network", "inspect", self.network_id])),
                                       identity=self.network_id, nonce=self.nonce)
        self.container_id = self.command(["docker", "run", "--detach", "--pull=never", "--name", self.name,
            "--network", self.network_id, "--label", "hirewiz.stage5.owner=" + self.nonce,
            "--publish", f"127.0.0.1:{self.emulator_port}:8080", "--cpus=1", "--memory=2g",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--env", "CLOUDSDK_CONFIG=/tmp/cloudsdk",
            "--env", "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING=true", "--env", "CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK=true",
            "--env", "JAVA_TOOL_OPTIONS=-Xmx1536m", "--workdir=/tmp", EMULATOR,
            "gcloud", "emulators", "firestore", "start", "--project=hirewiz-local-authority",
            "--host-port=0.0.0.0:8080", "--quiet"]).strip()
        assert self.container_id is not None
        metadata = json.loads(self.command(["docker", "inspect", self.container_id]))[0]
        networks = metadata.get("NetworkSettings", {}).get("Networks", {})
        if (metadata["Image"] != configuration_id or
                metadata["Config"]["Labels"].get("hirewiz.stage5.owner") != self.nonce or
                len(networks) != 1 or next(iter(networks.values())).get("NetworkID") != self.network_id or
                metadata["HostConfig"]["PortBindings"] != {
                    "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.emulator_port)}]}):
            raise RuntimeError("Owned emulator binding differs")
        self.emulator_identity = {"image": metadata["Image"], "manifest": EMULATOR_REPO_DIGEST,
                                  "container_id": self.container_id,
                                  "network_internal": True, "loopback_port": self.emulator_port}

    def close(self):
        # IDs and nonce labels, not names alone, are required before Docker deletion.
        failures = []
        if self.container_id:
            try:
                metadata = json.loads(self.command(["docker", "inspect", self.container_id]))[0]
                if metadata["Id"] != self.container_id or metadata["Config"]["Labels"].get("hirewiz.stage5.owner") != self.nonce:
                    raise RuntimeError("Owned emulator identity changed; cleanup refused")
                self.command(["docker", "rm", "--force", self.container_id])
                self.cleanup.append({"emulator_deleted": self.container_id})
                self.container_id = None
            except Exception:
                failures.append("emulator")
                self.cleanup.append({"component": "emulator", "cleanup": "refused_or_unknown"})
        if self.network_id:
            try:
                network = json.loads(self.command(["docker", "network", "inspect", self.network_id]))[0]
                if network["Id"] != self.network_id or network["Labels"].get("hirewiz.stage5.owner") != self.nonce or network["Containers"]:
                    raise RuntimeError("Owned emulator network changed; cleanup refused")
                self.command(["docker", "network", "rm", self.network_id])
                self.cleanup.append({"network_deleted": self.network_id})
                self.network_id = None
            except Exception:
                failures.append("network")
                self.cleanup.append({"component": "network", "cleanup": "refused_or_unknown"})
        for process in reversed(self.processes):
            try:
                self.cleanup.append(_stop(process))
            except Exception:
                failures.append("process_group")
                self.cleanup.append({"component": "process_group", "cleanup": "refused_or_unknown"})
        for log in self.logs:
            log.close()
        data = getattr(self, "pg_data", None)
        if not failures and data is not None and data.is_dir():
            try:
                owner = json.loads((self.directory / "ownership.json").read_text())
                if (data != self.directory / "pg-data" or data.is_symlink()
                        or data.stat().st_uid != os.getuid() or owner["nonce"] != self.nonce
                        or (data / "PG_VERSION").read_text().strip() != "17"
                        or (data / "postmaster.pid").exists()):
                    raise RuntimeError("Owned stopped scratch identity differs")
                control = self.command([str(self.pg_bin / "pg_controldata"), "-D", str(data)])
                fields = dict(line.split(":", 1) for line in control.splitlines() if ":" in line)
                anchor = getattr(self, "pg_system_identifier", None)
                retained = json.loads((self.directory / "pg-creation-identity.json").read_text())
                if (type(anchor) is not str or not anchor
                        or retained != {"nonce": self.nonce, "system_identifier": anchor}
                        or fields["Database system identifier"].strip() != anchor
                        or fields["Database cluster state"].strip() != "shut down"):
                    raise RuntimeError("Original owned PostgreSQL identity/shutdown differs")
                shutil.rmtree(data)
                self.cleanup.append({"pg_scratch_removed": True,
                    "local_system_identifier": fields["Database system identifier"].strip()})
            except Exception:
                failures.append("pg_scratch")
                self.cleanup.append({"component": "pg_scratch", "cleanup": "refused_or_unknown"})
        if failures:
            raise RuntimeError("Owned dependency cleanup refused or remains unknown")
