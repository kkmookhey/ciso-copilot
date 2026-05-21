#!/usr/bin/env python3
"""Mirror scripts/check_titles.py into the findings Lambdas.

Each Lambda bundles only its own directory, so the catalog is duplicated
rather than imported from a shared path or layer. This script is the only
sanctioned way to update those copies — run it after any edit to the
master catalog. Idempotent.
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
MASTER = _REPO / "scripts" / "check_titles.py"
TARGETS = [
    _REPO / "platform" / "lambda" / "findings_list" / "check_titles.py",
    _REPO / "platform" / "lambda" / "findings_rollup" / "check_titles.py",
]


def sync() -> None:
    data = MASTER.read_bytes()
    for target in TARGETS:
        target.write_bytes(data)
        print(f"synced -> {target.relative_to(_REPO)}")


if __name__ == "__main__":
    sync()
