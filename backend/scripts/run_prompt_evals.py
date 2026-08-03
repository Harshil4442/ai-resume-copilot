from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domains.analysis.evaluation import evaluate_golden_cases  # noqa: E402


def main() -> int:
    path = ROOT / "evals" / "golden_cases.json"
    result = evaluate_golden_cases(path)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
