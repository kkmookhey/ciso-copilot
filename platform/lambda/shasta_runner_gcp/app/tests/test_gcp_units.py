import pytest
from gcp_units import ALL_MODULES, modules_for_tier


def test_quick_splits_into_two_phases():
    phase1, phase2 = modules_for_tier("quick")
    assert phase1 == ["iam", "storage"]
    assert phase2 == ["networking", "encryption", "compute"]


def test_medium_runs_all_modules_in_phase_one():
    phase1, phase2 = modules_for_tier("medium")
    assert set(phase1) == set(ALL_MODULES)
    assert phase2 == []


def test_deep_runs_all_modules_in_phase_one():
    phase1, phase2 = modules_for_tier("deep")
    assert set(phase1) == set(ALL_MODULES)
    assert phase2 == []


def test_tier_is_case_insensitive():
    assert modules_for_tier("QUICK") == modules_for_tier("quick")


def test_unknown_tier_raises():
    with pytest.raises(ValueError, match="unknown scan tier"):
        modules_for_tier("turbo")


def test_modules_for_tier_returns_fresh_lists():
    phase1, _ = modules_for_tier("quick")
    phase1.append("mutated")
    phase1_again, _ = modules_for_tier("quick")
    assert "mutated" not in phase1_again
