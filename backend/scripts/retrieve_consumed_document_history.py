"""Download only the five pinned historical artifacts; never replay their work."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra/gcp"))
import document_consumed_history as history

REPOSITORY = "Harshil4442/ai-resume-copilot"


def github(path, *, binary=False):
    result = subprocess.run(["gh", "api", "repos/" + REPOSITORY + "/" + path],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False)
    history.need(result.returncode == 0 and 0 < len(result.stdout) <= 4194304,
                 "exact_artifact_download_unavailable")
    return result.stdout if binary else json.loads(result.stdout)


def metadata(pin, value, run):
    history.need(value.get("workflow_run", {}).get("id") == pin.run and value.get("name") == pin.name
                 and (pin.artifact is None or value.get("id") == pin.artifact)
                 and value.get("workflow_run", {}).get("head_sha") == pin.source
                 and value.get("workflow_run", {}).get("head_branch") == "main"
                 and value.get("digest") == "sha256:" + pin.zip_sha256 and value.get("expired") is False
                 and type(value.get("size_in_bytes")) is int and 0 < value["size_in_bytes"] <= 2097152
                 and run.get("id") == pin.run and run.get("head_sha") == pin.source
                 and run.get("head_branch") == "main" and run.get("status") == "completed"
                 and run.get("conclusion") == ("success" if pin.run == 40811330747 else "failure")
                 and run.get("path") == ".github/workflows/document-refresh.yml"
                 and run.get("repository", {}).get("full_name") == REPOSITORY,
                 "exact_artifact_metadata_required")


def unpack(raw, directory, pin):
    history.need(hashlib.sha256(raw).hexdigest() == pin.zip_sha256, "exact_artifact_archive_required")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files: dict[str, bytes] = {}
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            history.need(not name.is_absolute() and ".." not in name.parts and "\\" not in item.filename
                         and len(name.parts) <= 6 and not stat.S_ISLNK(item.external_attr >> 16), "archive_path_refused")
            if item.is_dir():
                continue
            history.need(item.filename not in files and name.suffix == ".json"
                         and 0 < item.file_size <= 1048576 and len(files) < 100,
                         "archive_file_refused")
            files[item.filename] = archive.read(item)
            history.need(sum(map(len, files.values())) <= 2097152, "archive_size_bound")
    history.need(len(files) == pin.count and history.tree(files) == pin.tree_sha256,
                 "exact_artifact_bytes_required")
    directory.mkdir(mode=0o700)
    for filename, data in files.items():
        target = directory / filename
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(data)
        target.chmod(0o600)


def retrieve(directory, prior_history=None, *, force=False, get=github):
    history.need(directory.is_absolute() and not directory.exists(), "fresh_bundle_required")
    if not force and not history.required(prior_history):
        return False
    directory.mkdir(mode=0o700)
    for pin in history.ARTIFACTS:
        if pin.artifact is None:
            candidates = get(f"actions/runs/{pin.run}/artifacts?per_page=100").get("artifacts", [])
            candidates = [row for row in candidates if row.get("name") == pin.name]
            history.need(len(candidates) == 1 and type(candidates[0].get("id")) is int
                         and candidates[0]["id"] > 0, "exact_readback_artifact_required")
            artifact = get("actions/artifacts/" + str(candidates[0]["id"]))
        else:
            artifact = get("actions/artifacts/" + str(pin.artifact))
        run = get("actions/runs/" + str(pin.run))
        metadata(pin, artifact, run)
        archive = get(f"actions/artifacts/{artifact['id']}/zip", binary=True)
        unpack(archive, directory / str(pin.run), pin)
    history.load_bundle(directory)
    return True


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--prior-history-directory", type=Path)
    parser.add_argument("--force-audit", action="store_true")
    args = parser.parse_args()
    try:
        retrieved = retrieve(args.output_directory, args.prior_history_directory, force=args.force_audit)
        print(json.dumps({"exact_audit_bundle_retrieved": retrieved, "read_only_retrieval": True,
                          "history_consumed": False, "automatic_replay": False}))
    except Exception:
        print(json.dumps({"fixed_code": "document_consumed_history_retrieval_unavailable", "automatic_replay": False}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
