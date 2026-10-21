"""Capture/advance processes must agree without native clients or writes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = r'''
import json
import sys
from dataclasses import asdict
from pathlib import Path

root = Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / "infra/gcp"), str(root / "backend")]
from backend.tests.test_rolling_release import FixtureBoundary, _candidate_policy_plan
import browser_schema19_release as reviewed

rolling = reviewed.rolling
boundary = FixtureBoundary()
added = set(reviewed.OFF) - set(rolling.OFF)
assert len(added) == 8
for service in rolling.release.SERVICES[1:]:
    assert added.isdisjoint(rolling.environment(boundary.states[service]["template"]))

if sys.argv[2] == "capture":
    boundary.plan = reviewed.load_plan(rolling.canonical(asdict(
        reviewed.Schema19Plan(**asdict(boundary.plan)))))
    plan = _candidate_policy_plan(boundary)
else:
    # Keep the exact candidate hashes captured by the other process.
    plan = reviewed.load_plan(sys.stdin.buffer.read())

bodies = {}
hashes = {}
for service in rolling.release.SERVICES[1:]:
    current = boundary.service(service)
    before = rolling.canonical(current)
    body = rolling.stage_body(plan, plan.pin(service), current)
    env = rolling.environment(body["template"])
    assert all(env.get(name, {"value": "false"})["value"] == "false"
               for name in reviewed.OFF)
    assert all(env[name]["value"] == "false" for name in added)
    assert env["HIREWIZ_EXPENSE_POLICY_JSON"]["valueSource"]["secretKeyRef"]["version"] == "2"
    assert rolling.canonical(current) == before
    assert body["traffic"] == rolling._traffic_for(plan, plan.pin(service), False)
    bodies[service] = body
    hashes[service] = rolling.template_sha(body["template"], service)
assert boundary.writes == []
print(json.dumps({"plan": asdict(plan), "bodies": bodies, "hashes": hashes}, sort_keys=True))
'''


def _process(seed: str, mode: str, plan: dict | None = None) -> dict:
    # -I would ignore PYTHONHASHSEED and conceal this cross-process regression.
    environment = {
        "PATH": os.defpath,
        "PYTHONHASHSEED": seed,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", SCRIPT, str(ROOT), mode],
        input=json.dumps(plan) if plan is not None else None,
        capture_output=True,
        text=True,
        env=environment,
        cwd=ROOT / "backend",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_schema19_worker_stage_retains_capture_hash_across_python_hash_seeds():
    captured = _process("1", "capture")
    advanced = _process("2", "advance", captured["plan"])

    assert advanced["plan"] == captured["plan"]
    assert advanced["bodies"] == captured["bodies"]
    assert advanced["hashes"] == captured["hashes"]
    for service, digest in advanced["hashes"].items():
        pin = next(pin for pin in captured["plan"]["services"] if pin["service"] == service)
        assert digest == pin["candidate_template_sha256"]
