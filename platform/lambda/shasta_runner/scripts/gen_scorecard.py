# platform/lambda/shasta_runner/scripts/gen_scorecard.py
"""Regenerate the committed AWS coverage scorecard.

Coverage today = the Shasta manifest only (the coverage engine has no
checks yet — Slice 1+). As the engine's registry fills, add its checks
to the coverage map here.

Usage: python scripts/gen_scorecard.py
Writes docs/coverage/aws-scorecard.{md,json} at the repo root.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))

from coverage.scorecard import compute_scorecard, load_catalogs, render_markdown
from coverage.shasta_manifest import SHASTA_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[4]
_OUT_DIR = _REPO_ROOT / "docs" / "coverage"


def build_coverage_map() -> dict[str, dict[str, list[str]]]:
    """The coverage map the scorecard scores. Today: Shasta only."""
    return dict(SHASTA_CHECKS)


def main() -> None:
    scorecard = compute_scorecard(load_catalogs(), build_coverage_map())
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / "aws-scorecard.json").write_text(
        json.dumps(scorecard, indent=2, sort_keys=True) + "\n"
    )
    (_OUT_DIR / "aws-scorecard.md").write_text(render_markdown(scorecard))
    for name, b in scorecard["benchmarks"].items():
        print(f"{name}: {b['covered']}/{b['total']} ({b['coverage_pct']}%)")


if __name__ == "__main__":
    main()
