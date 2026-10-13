"""CI-only PG17 client containers for UUID-owned synthetic transfer tests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

IMAGE = "public.ecr.aws/docker/library/postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
CLIENT = r'''#!/usr/bin/python3
import os,re,subprocess,sys
from pathlib import Path
DOCKER = __DOCKER__
CONFIG = __CONFIG__
WORK = __WORK__
IMAGE = __IMAGE__
LABEL = __LABEL__
USER = __USER__
CLIENT = __CLIENT__
args=sys.argv[1:]
def refuse():
    raise SystemExit(2)
def private_path(value):
    path=Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(WORK):
        refuse()
if CLIENT == "pg_dump":
    if len(args)!=8 or args[:4]!=["--format=custom","--no-password","--no-privileges","--lock-wait-timeout=4000ms"] or args[4]!="--snapshot" or not re.fullmatch(r"[a-fA-F0-9-]{1,96}",args[5]) or args[6]!="--file":
        refuse()
    private_path(args[7])
elif args[:1]==["--list"]:
    if len(args)!=2:
        refuse()
    private_path(args[1])
else:
    if len(args) not in (10,12) or args[:5]!=["--single-transaction","--exit-on-error","--no-password","--no-owner","--no-acl"] or args[5]!="--use-list" or args[7]!="--dbname" or args[8]!=os.environ.get("PGDATABASE"):
        refuse()
    if len(args)==12 and (args[9]!="--role" or not re.fullmatch(r"hw_transfer_migrator_[a-f0-9]{32}",args[10])):
        refuse()
    private_path(args[6]);private_path(args[-1])
if args[:1]!=["--list"]:
    source=CLIENT=="pg_dump"
    options="-c default_transaction_read_only="+("on " if source else "off ")+"-c statement_timeout=300000 -c lock_timeout=4000 -c idle_in_transaction_session_timeout=1810000 -c search_path=pg_catalog"
    database=r"hw_transfer_[a-f0-9]{32}_"+("source" if source else "target")
    actor=r"hirewiz" if source else r"hw_transfer_writer_[a-f0-9]{32}"
    if os.environ.get("PGHOST")!="127.0.0.1" or os.environ.get("PGPORT")!="55433" or os.environ.get("PGSSLMODE")!="disable" or not re.fullmatch(database,os.environ.get("PGDATABASE","")) or not re.fullmatch(actor,os.environ.get("PGUSER","")):
        refuse()
    if os.environ.get("PGOPTIONS")!=options or os.environ.get("PGCONNECT_TIMEOUT")!="5" or os.environ.get("PGAPPNAME")!="hirewiz_owned_database_transfer" or os.environ.get("PGSSLCERTMODE")!="disable" or os.environ.get("PGCHANNELBINDING")!="prefer":
        refuse()
command=[DOCKER,"--config",CONFIG,"--host","unix:///var/run/docker.sock","run","--rm","--pull=never","--network=host","--read-only","--ulimit","fsize=536870912:536870912","--cap-drop=ALL","--security-opt=no-new-privileges","--user",USER,"--label",LABEL,"--mount","type=bind,src="+WORK+",dst="+WORK,"--tmpfs","/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777"]
# Names only: fixture passwords never become process arguments or log output.
for key in ("PGHOST","PGPORT","PGDATABASE","PGUSER","PGPASSWORD","PGOPTIONS","PGCONNECT_TIMEOUT","PGAPPNAME","PGSSLMODE","PGSSLCERTMODE","PGCHANNELBINDING"):
    if key in os.environ:
        command.extend(["--env",key])
command.extend(["--entrypoint","/usr/local/bin/"+CLIENT,IMAGE,*args])
raise SystemExit(subprocess.call(command))
'''


def client_source(docker: str, root: Path, label: str, user: str, client: str) -> str:
    values = {"DOCKER": docker, "CONFIG": str(root / "docker-config"), "WORK": str(root / "work"),
              "IMAGE": IMAGE, "LABEL": label, "USER": user, "CLIENT": client}
    result = CLIENT
    for key, value in values.items():
        result = result.replace("__" + key + "__", repr(value))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-container", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if (os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("RUNNER_OS") != "Linux"
            or not re.fullmatch(r"[a-f0-9]{64}", args.service_container)
            or not args.root.is_absolute() or any(c in str(args.root) for c in "\n\r,")):
        raise SystemExit("CI transfer scope refused")
    if args.root.is_symlink():
        raise SystemExit("CI transfer scope refused")
    # No wrapper/container can exist before preparation creates this fresh root.
    # A successful cleanup step never changes GitHub's preceding failure result.
    if args.cleanup and not args.root.exists():
        return
    docker = shutil.which("docker")
    if not docker:
        raise SystemExit("CI Docker client unavailable")
    docker = str(Path(docker).resolve(strict=True))
    label = "hirewiz.transfer-test=" + hashlib.sha256(
        (str(args.root) + args.service_container).encode()).hexdigest()[:24]
    prefix = [docker, "--config", str(args.root / "docker-config"),
              "--host", "unix:///var/run/docker.sock"]
    if args.cleanup:
        result = subprocess.run([*prefix, "ps", "--all", "--filter", "label=" + label,
            "--format", "{{.ID}}"], capture_output=True, timeout=10, check=True)
        ids = result.stdout.decode("ascii").splitlines()
        if len(ids) > 32 or any(not re.fullmatch(r"[a-f0-9]{12,64}", ident) for ident in ids):
            raise SystemExit("CI cleanup scope refused")
        if ids:
            subprocess.run([*prefix, "rm", "--force", *ids], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15, check=True)
        return
    result = subprocess.run([*prefix, "inspect", "--format",
        "[{{json .Id}},{{json .Config.Image}},{{json .State.Running}},{{json .State.Health.Status}}]",
        args.service_container], capture_output=True, timeout=10, check=True)
    if len(result.stdout) > 4096 or json.loads(result.stdout) != [args.service_container, IMAGE, True, "healthy"]:
        raise SystemExit("Owned PG17 service mismatch")
    args.root.mkdir(mode=0o700)
    (args.root / "work").mkdir(mode=0o700)
    (args.root / "docker-config").mkdir(mode=0o700)
    with Path(os.environ["GITHUB_ENV"]).open("a") as output:
        for client, key in (("pg_dump", "HIREWIZ_TRANSFER_PG_DUMP"),
                            ("pg_restore", "HIREWIZ_TRANSFER_PG_RESTORE")):
            path = args.root / client
            path.write_text(client_source(docker, args.root, label, f"{os.getuid()}:{os.getgid()}", client))
            path.chmod(0o500)
            output.write(key + "=" + str(path) + "\n")
        output.write("HIREWIZ_TRANSFER_WORKDIR=" + str(args.root / "work") + "\n")
        output.write("HIREWIZ_TRANSFER_POSTGRES_REQUIRED=true\n")


if __name__ == "__main__":
    main()
