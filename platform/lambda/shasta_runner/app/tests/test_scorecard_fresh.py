# app/tests/test_scorecard_fresh.py
"""The committed scorecard must match a fresh regeneration — so coverage
% cannot silently rot. If this fails, run: python scripts/gen_scorecard.py"""
import json
from pathlib import Path

from coverage.scorecard import compute_scorecard, load_catalogs
from coverage.shasta_manifest import SHASTA_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCORECARD_JSON = _REPO_ROOT / "docs" / "coverage" / "aws-scorecard.json"


def test_committed_scorecard_is_current():
    fresh = compute_scorecard(load_catalogs(), dict(SHASTA_CHECKS))
    committed = json.loads(_SCORECARD_JSON.read_text())
    assert committed == fresh, "stale scorecard — run scripts/gen_scorecard.py"
