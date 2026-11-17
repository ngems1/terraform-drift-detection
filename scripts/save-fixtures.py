"""Copy the plans produced by the e2e test into tests/fixtures/plans/, normalised.

    make fixtures   (runs the e2e test, then this script)

Normalising (drop `timestamp`, sort `relevant_attributes`) keeps the fixtures byte-stable, so
a diff in a pull request means Terraform's output really changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".e2e"
DST = ROOT / "tests" / "fixtures" / "plans"


def main() -> int:
    DST.mkdir(parents=True, exist_ok=True)
    for name in ("clean", "drift", "pending"):
        src = SRC / f"plan-{name}.json"
        if not src.is_file():
            print(f"missing {src}: run tests/e2e/run.sh first", file=sys.stderr)
            return 1
        data = json.loads(src.read_text(encoding="utf-8"))
        data.pop("timestamp", None)
        data["relevant_attributes"] = sorted(data.get("relevant_attributes", []), key=json.dumps)
        (DST / f"{name}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote tests/fixtures/plans/{name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
