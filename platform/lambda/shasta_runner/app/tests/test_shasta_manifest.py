# app/tests/test_shasta_manifest.py
"""The Shasta manifest enumerates every Shasta cloud check and the
benchmark controls it covers. Each entry's framework keys must be a
subset of the known benchmark names; control ids are plain strings."""
from coverage.shasta_manifest import SHASTA_CHECKS

_BENCHMARKS = {"cis_aws", "fsbp", "pci_dss", "nist_800_53"}


def test_manifest_covers_every_shasta_check():
    # 113 = the complete set of non-ai_checks Shasta AWS check_ids (spec §6 gap analysis).
    assert len(SHASTA_CHECKS) >= 113


def test_manifest_entries_are_well_formed():
    for check_id, entry in SHASTA_CHECKS.items():
        assert isinstance(check_id, str) and check_id
        assert isinstance(entry, dict)
        assert set(entry).issubset(_BENCHMARKS), f"{check_id}: unknown benchmark key"
        for controls in entry.values():
            assert isinstance(controls, list)
            assert all(isinstance(c, str) and c for c in controls)
