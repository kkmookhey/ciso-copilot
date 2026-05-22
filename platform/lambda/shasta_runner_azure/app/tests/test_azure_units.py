"""azure_units maps a scan tier to which Shasta Azure modules run, split
into the two Quick phases."""
import pytest

from azure_units import ALL_MODULES, modules_for_tier


def test_quick_is_two_phase():
    p1, p2 = modules_for_tier("quick")
    assert p1 == ["iam", "governance"]
    assert p2 == ["storage", "networking", "compute", "encryption"]


def test_medium_is_single_phase_nine_modules():
    p1, p2 = modules_for_tier("medium")
    assert p2 == []
    assert len(p1) == 9
    assert set(p1) == {"iam", "governance", "storage", "networking",
                       "compute", "encryption", "databases",
                       "appservice", "monitoring"}


def test_deep_is_single_phase_all_twelve():
    p1, p2 = modules_for_tier("deep")
    assert p2 == []
    assert len(p1) == 12
    assert set(p1) == set(ALL_MODULES)


def test_tier_is_case_insensitive():
    assert modules_for_tier("QUICK") == modules_for_tier("quick")


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        modules_for_tier("turbo")
