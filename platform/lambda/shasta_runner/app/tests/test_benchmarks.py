# app/tests/test_benchmarks.py
"""The four benchmark catalogs must exist and be shaped uniformly:
a JSON list of {"id": str, "title": str} objects with unique, non-empty ids."""
import json
from pathlib import Path

import pytest

_BENCH_DIR = Path(__file__).resolve().parent.parent / "coverage" / "benchmarks"
_CATALOGS = ["cis_aws", "fsbp", "pci_dss", "nist_800_53"]
_MIN_CONTROLS = {"cis_aws": 55, "fsbp": 300, "pci_dss": 230, "nist_800_53": 300}


@pytest.mark.parametrize("name", _CATALOGS)
def test_catalog_is_well_formed(name):
    path = _BENCH_DIR / f"{name}.json"
    assert path.exists(), f"missing catalog {path}"
    controls = json.loads(path.read_text())
    assert isinstance(controls, list) and controls, f"{name}: not a non-empty list"
    ids = [c["id"] for c in controls]
    assert all(isinstance(c["id"], str) and c["id"] for c in controls), f"{name}: bad id"
    assert all(isinstance(c["title"], str) and c["title"] for c in controls), f"{name}: bad title"
    assert len(ids) == len(set(ids)), f"{name}: duplicate ids"
    assert len(controls) >= _MIN_CONTROLS[name], (
        f"{name}: only {len(controls)} controls — catalog may be truncated"
    )
