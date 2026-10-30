"""Only fresh invocation-owned loopback dependencies; no caller database/Redis URI."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO
from uuid import uuid4

from scripts.run_cold_browser_journey import _owned_status, _spawn_owned, _stop
from scripts.stage5_relay import validate_target

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


def internal_endpoint(container, network, *, container_id, network_id, nonce):
    values = container.get("NetworkSettings", {}).get("Networks", {})
    if (container.get("Id") != container_id or len(values) != 1
            or container.get("Config", {}).get("Labels", {}).get("hirewiz.stage5.owner") != nonce
            or network.get("Id") != network_id or network.get("Internal") is not True
            or network.get("Driver") != "bridge"
            or network.get("Labels", {}).get("hirewiz.stage5.owner") != nonce
            or set(network.get("Containers", {})) != {container_id}):
        raise RuntimeError("Current owned emulator membership differs")
    entry = next(iter(values.values()))
    subnets = network.get("IPAM", {}).get("Config", [])
    if entry.get("NetworkID") != network_id or len(subnets) != 1:
        raise RuntimeError("Current owned emulator subnet differs")
    address, subnet = entry.get("IPAddress"), subnets[0].get("Subnet")
    target = validate_target(address, subnet, 8080)
    if (network["Containers"][container_id].get("IPv4Address") != f"{address}/{subnet.split('/')[1]}"
            or address == subnets[0].get("Gateway")):
        raise RuntimeError("Current owned emulator address differs")
    return target, subnet


def effective_publication(container, port):
    actual = container.get("NetworkSettings", {}).get("Ports", {}).get("8080/tcp")
    if actual in (None, []):
        return False
    if actual != [{"HostIp": "127.0.0.1", "HostPort": str(port)}]:
        raise RuntimeError("Effective emulator publication is not exact loopback")
    return True


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
        if sys.platform != "linux":
            raise RuntimeError("The isolated emulator transport requires a native Linux Docker host")
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
        diagnostic = {"image": metadata["Image"], "manifest": EMULATOR_REPO_DIGEST,
                      "container_id": self.container_id, "network_id": self.network_id,
                      "network_internal": True, "effective_publication": None,
                      "namespace_tcp_ready": None, "published_host_tcp_ready": None,
                      "transport": "not_selected"}
        try:
            network = json.loads(self.command(["docker", "network", "inspect", self.network_id]))[0]
            target, subnet = internal_endpoint(metadata, network, container_id=self.container_id,
                network_id=self.network_id, nonce=self.nonce)
            diagnostic["effective_publication"] = effective_publication(metadata, self.emulator_port)
            program = ("import socket,time,json\nend=time.monotonic()+20\n"
                "while time.monotonic()<end:\n"
                " try:\n  s=socket.create_connection(('127.0.0.1',8080),timeout=.5);s.close();break\n"
                " except OSError:time.sleep(.1)\nelse:raise RuntimeError('not_ready')\n"
                "print(json.dumps({'loopback_tcp_ready':True}))")
            ready = json.loads(self.command(["docker", "exec", self.container_id, "python3", "-c", program], seconds=25))
            if ready != {"loopback_tcp_ready": True}:
                raise RuntimeError("Owned emulator namespace readiness differs")
            diagnostic["namespace_tcp_ready"] = True
            published = False
            if diagnostic["effective_publication"]:
                try:
                    with socket.create_connection(("127.0.0.1", self.emulator_port), timeout=2):
                        published = True
                except OSError:
                    pass
                diagnostic["published_host_tcp_ready"] = published
            if published:
                diagnostic["transport"] = "verified_docker_loopback_publication"
            else:
                # A new native readback immediately precedes relay selection.
                metadata = json.loads(self.command(["docker", "inspect", self.container_id]))[0]
                network = json.loads(self.command(["docker", "network", "inspect", self.network_id]))[0]
                if metadata["Image"] != configuration_id:
                    raise RuntimeError("Current emulator image differs")
                target, subnet = internal_endpoint(metadata, network, container_id=self.container_id,
                    network_id=self.network_id, nonce=self.nonce)
                with socket.create_connection(target, timeout=2):
                    pass
                self.emulator_port = unused_port()
                if self.emulator_port in {self.pg_port, self.api_port}:
                    raise RuntimeError("Owned relay port collision")
                config = {"target_ip": target[0], "subnet": subnet, "target_port": 8080,
                          "listen_port": self.emulator_port, "nonce": self.nonce,
                          "network_id": self.network_id, "container_id": self.container_id}
                config_path, ready_path = self.directory / "relay-config.json", self.directory / "relay-ready.json"
                private_json(config_path, config)
                config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
                child = self.spawn([sys.executable, "-B", str(Path(__file__).with_name("stage5_relay.py")),
                    str(config_path), config_hash, str(ready_path)], "emulator-relay")
                deadline = time.monotonic() + 5
                while not ready_path.exists():
                    if _owned_status(child) is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Owned emulator relay readiness failed")
                    time.sleep(.05)
                if (_owned_status(child) is not None or json.loads(ready_path.read_text()) != {
                        "nonce": self.nonce, "pid": child.pid, "port": self.emulator_port}):
                    raise RuntimeError("Owned emulator relay identity differs")
                diagnostic.update(transport="owned_fixed_loopback_relay", relay_pid=child.pid,
                                  target_ip=target[0], target_subnet=subnet)
            diagnostic["loopback_port"] = self.emulator_port
            self.emulator_identity = diagnostic
        finally:
            private_json(self.directory / "emulator-transport.json", diagnostic)

    def close(self):
        # IDs and nonce labels, not names alone, are required before Docker deletion.
        failures = []
        # Stop the fixed relay before releasing its container IP for reuse.
        for process in reversed(self.processes):
            try:
                self.cleanup.append(_stop(process))
            except Exception:
                failures.append("process_group")
                self.cleanup.append({"component": "process_group", "cleanup": "refused_or_unknown"})
        if self.container_id and not failures:
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
        elif self.container_id:
            self.cleanup.append({"component": "emulator", "cleanup": "held_for_owned_process_stop"})
        if self.network_id and not failures:
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
        elif self.network_id:
            self.cleanup.append({"component": "network", "cleanup": "held_for_dependency_cleanup"})
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
