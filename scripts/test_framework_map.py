"""Tests for the FedRAMP / PCI DSS framework-mapping catalog."""
import re
from pathlib import Path

import pytest

import framework_map

SHASTA_SRC = Path.home() / "Projects" / "Shasta" / "src"


def _shasta_check_ids() -> set[str]:
    if not SHASTA_SRC.is_dir():
        pytest.skip(f"Shasta source not found at {SHASTA_SRC}")
    ids: set[str] = set()
    for py in SHASTA_SRC.rglob("*.py"):
        ids |= set(re.findall(r"""check_id=['"]([a-z0-9_-]+)['"]""",
                              py.read_text()))
    return ids


# --- merge_framework_map ----------------------------------------------------

def test_merge_leaves_unmapped_check_unchanged():
    result = framework_map.merge_framework_map(
        "totally-unknown-check-xyz", {"soc2": ["CC6.1"]})
    assert result == {"soc2": ["CC6.1"]}


def test_merge_adds_fedramp_and_pci_for_a_mapped_check():
    mapped_id = next(iter(framework_map.FRAMEWORK_MAP))
    expected  = framework_map.FRAMEWORK_MAP[mapped_id]
    result = framework_map.merge_framework_map(mapped_id, {"soc2": ["CC6.1"]})
    assert result["soc2"] == ["CC6.1"]                  # existing preserved
    for fw, controls in expected.items():
        assert result[fw] == controls


def test_merge_does_not_mutate_its_input():
    original = {"soc2": ["CC6.1"]}
    mapped_id = next(iter(framework_map.FRAMEWORK_MAP))
    framework_map.merge_framework_map(mapped_id, original)
    assert original == {"soc2": ["CC6.1"]}


# --- catalog completeness + quality -----------------------------------------

def test_catalog_keys_are_known_shasta_check_ids():
    extra = set(framework_map.FRAMEWORK_MAP) - _shasta_check_ids()
    assert not extra, f"catalog keys not present in Shasta: {sorted(extra)}"


def test_catalog_entries_use_only_recognised_frameworks():
    bad = {k: sorted(set(v) - {"fedramp", "pci_dss"})
           for k, v in framework_map.FRAMEWORK_MAP.items()
           if set(v) - {"fedramp", "pci_dss"}}
    assert not bad, f"unrecognised framework keys: {bad}"


def test_catalog_has_no_empty_control_lists():
    empty = [(k, fw) for k, v in framework_map.FRAMEWORK_MAP.items()
             for fw, controls in v.items() if not controls]
    assert not empty, f"empty control lists: {empty}"


def test_fedramp_control_ids_are_well_formed():
    bad = [(k, c) for k, v in framework_map.FRAMEWORK_MAP.items()
           for c in v.get("fedramp", [])
           if not re.match(r"^[A-Z]{2}-\d+$", c)]
    assert not bad, f"malformed NIST 800-53 control IDs: {bad}"


def test_pci_requirement_ids_are_well_formed():
    bad = [(k, c) for k, v in framework_map.FRAMEWORK_MAP.items()
           for c in v.get("pci_dss", [])
           if not re.match(r"^\d+\.\d+(\.\d+)?$", c)]
    assert not bad, f"malformed PCI DSS requirement IDs: {bad}"


# --- sync to the scanner Lambdas --------------------------------------------

_REPO = Path(__file__).resolve().parent.parent
_MASTER = _REPO / "scripts" / "framework_map.py"
_SCANNER_COPIES = [
    _REPO / "platform" / "lambda" / s / "app" / "framework_map.py"
    for s in ("shasta_runner", "shasta_runner_azure",
              "shasta_runner_gcp", "shasta_runner_entra")
]


@pytest.mark.parametrize("copy_path", _SCANNER_COPIES,
                         ids=lambda p: p.parent.parent.name)
def test_scanner_copy_is_byte_identical_to_master(copy_path):
    assert copy_path.exists(), (
        f"{copy_path} missing — run scripts/sync_framework_map.py"
    )
    assert copy_path.read_bytes() == _MASTER.read_bytes(), (
        f"{copy_path} has drifted — run scripts/sync_framework_map.py"
    )
