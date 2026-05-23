"""azure_units maps a scan tier to which Shasta Azure modules run, split
into the two Quick phases."""
import pytest

from azure_units import ALL_MODULES, modules_for_tier


def test_quick_is_two_phase():
    p1, p2 = modules_for_tier("quick")
    assert p1 == ["iam", "governance"]
    assert p2 == ["storage", "networking", "compute", "encryption"]


def test_medium_is_single_phase_ten_modules():
    p1, p2 = modules_for_tier("medium")
    assert p2 == []
    assert len(p1) == 10
    assert set(p1) == {"iam", "governance", "storage", "networking",
                       "compute", "encryption", "databases",
                       "appservice", "monitoring", "ai"}


def test_deep_is_single_phase_all_thirteen():
    p1, p2 = modules_for_tier("deep")
    assert p2 == []
    assert len(p1) == 13
    assert set(p1) == set(ALL_MODULES)


def test_tier_is_case_insensitive():
    assert modules_for_tier("QUICK") == modules_for_tier("quick")


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        modules_for_tier("turbo")


def test_ai_module_appears_in_medium_plus_only():
    quick_p1, quick_p2 = modules_for_tier("quick")
    assert "ai" not in (quick_p1 + quick_p2)

    medium_p1, medium_p2 = modules_for_tier("medium")
    assert "ai" in (medium_p1 + medium_p2)

    deep_p1, deep_p2 = modules_for_tier("deep")
    assert "ai" in (deep_p1 + deep_p2)
