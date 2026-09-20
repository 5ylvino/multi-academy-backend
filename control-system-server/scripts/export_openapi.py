#!/usr/bin/env python3
"""Export OpenAPI artifact for CI contract checks.

Usage (from multi-academy-control-system-server/):
  python scripts/export_openapi.py
  python scripts/export_openapi.py --check   # fail if artifact drifts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ARTIFACT = ROOT / "openapi" / "openapi.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Exit 1 if artifact differs")
    args = parser.parse_args()

    from app.main import create_app

    app = create_app()
    schema = app.openapi()
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not ARTIFACT.exists():
            print(f"Missing artifact: {ARTIFACT}", file=sys.stderr)
            return 1
        current = ARTIFACT.read_text(encoding="utf-8")
        if current != text:
            print("OpenAPI artifact is out of date. Run: python scripts/export_openapi.py", file=sys.stderr)
            return 1
        print("OpenAPI artifact is up to date.")
        return 0

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(text, encoding="utf-8")
    print(f"Wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
