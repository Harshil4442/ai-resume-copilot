"""Independent local process transport; stdin synthetic data, stdout status only."""

from __future__ import annotations

import json
import sys

from backend.tests.fixtures.candidate_ingress import connect, phase_classes

from app.domains.candidate_ingress.contracts import IngressResource
from app.domains.recovery.store import GuardDenied, GuardUnavailable


def main():
    spec = json.loads(sys.stdin.readline())
    c = connect(IngressResource.model_validate(spec["resource"]))
    try:
        print("READY", flush=True)
        if sys.stdin.readline() != "GO\n":
            raise RuntimeError("Synthetic coordination failed")
        try:
            c.ingress.authorize(
                spec["header"],
                method="POST",
                path=spec["path"].encode(),
                raw=spec["body"].encode(),
                authorization=spec["authorization"].encode(),
            )
            print(json.dumps({"decision": "accepted", "phase_classes": []}), flush=True)
        except GuardDenied as error:
            print(
                json.dumps({"decision": "denied", "phase_classes": phase_classes(error)}),
                flush=True,
            )
        except GuardUnavailable as error:
            print(
                json.dumps({"decision": "unknown", "phase_classes": phase_classes(error)}),
                flush=True,
            )
    finally:
        c.channel.close()


if __name__ == "__main__":
    main()
