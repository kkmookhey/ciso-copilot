"""Per-detector golden-file tests.

For each fixture under tests/fixtures/<detector_name>/<scenario>/:
  - repo/    a synthetic repo (small set of files)
  - expected.json   the emissions the detector should produce
"""
from __future__ import annotations

import importlib
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

# scan_runner reads GITHUB_APP_SECRET_ARN at import time. Set a harmless
# default so _make_ctx can import ScanContext for fixture runs.
os.environ.setdefault("GITHUB_APP_SECRET_ARN", "arn:test")

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


def test_fixtures_root_exists():
    """Guard against the fixture directory going missing and silently turning
    the parametrized suite green-by-vacancy."""
    assert FIXTURE_ROOT.exists(), f"fixtures dir missing: {FIXTURE_ROOT}"
    assert any(FIXTURE_ROOT.iterdir()), f"no detector fixtures under {FIXTURE_ROOT}"


def _scenarios():
    """Yield (detector_module, fixture_dir) pairs from every fixture."""
    for det_dir in FIXTURE_ROOT.iterdir():
        if not det_dir.is_dir():
            continue
        for scenario in det_dir.iterdir():
            if not scenario.is_dir():
                continue
            if not (scenario / "repo").is_dir():
                continue
            if not (scenario / "expected.json").exists():
                continue
            yield (det_dir.name, scenario)


def _make_ctx(repo_dir: Path):
    """Build a minimal ScanContext for fixture runs (no real GitHub)."""
    from scan_runner import ScanContext
    return ScanContext(
        scan_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        connection_id="33333333-3333-3333-3333-333333333333",
        repo_asset_id="44444444-4444-4444-4444-444444444444",
        repo_full_name="fixture/repo",
        default_branch="main",
        head_commit_sha="fixture-sha",
        installation_id=0,
        repo_workdir=repo_dir,
    )


def _normalise(result):
    """Convert DetectorResult to a stable dict for golden comparison."""
    def strip_dynamic(p):
        # Strip packet_id + produced_at — these are non-deterministic.
        p = {**p}
        p.pop("packet_id", None)
        p.pop("produced_at", None)
        if "subject" in p:
            p["subject"] = {**p["subject"]}
            p["subject"].pop("id", None)
        return p
    return {
        "assets": [
            {**asdict(a), "evidence_packet": strip_dynamic(a.evidence_packet)}
            for a in sorted(result.assets, key=lambda x: (x.asset_type, x.name, x.source_path or ""))
        ],
        "relationships": [
            {**asdict(r), "evidence_packet": strip_dynamic(r.evidence_packet)}
            for r in sorted(result.relationships, key=lambda x: (x.relationship_type, x.source_asset_ref, x.target_asset_ref))
        ],
        "findings": [
            {**asdict(f), "evidence_packet": strip_dynamic(f.evidence_packet)}
            for f in sorted(result.findings, key=lambda x: (x.finding_type, x.subject_ref))
        ],
    }


@pytest.mark.parametrize("detector_name,scenario", list(_scenarios()),
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_detector_golden(detector_name, scenario):
    module = importlib.import_module(f"detectors.{detector_name}")
    repo_dir = scenario / "repo"
    ctx = _make_ctx(repo_dir)
    result = module.detect(ctx)
    actual = _normalise(result)
    expected = json.loads((scenario / "expected.json").read_text())
    assert actual == expected, (
        f"Detector {detector_name} emission diverged on fixture {scenario.name}.\n"
        f"Actual:\n{json.dumps(actual, indent=2)}\n\nExpected:\n{json.dumps(expected, indent=2)}"
    )
