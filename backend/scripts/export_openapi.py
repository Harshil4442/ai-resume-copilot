from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///./openapi-export.db")
os.environ.setdefault("JWT_SECRET", "openapi-export-secret-at-least-thirty-two-characters")
os.environ.setdefault("FRONTEND_ORIGINS", "http://127.0.0.1:3000")

from app.main import app  # noqa: E402


def main() -> None:
    output = ROOT / "openapi.json"
    output.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
