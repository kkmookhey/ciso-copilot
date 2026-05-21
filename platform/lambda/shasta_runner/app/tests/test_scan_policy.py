# app/tests/test_scan_policy.py
"""The scan policy turns (scan_tier, region states) into a ScanPlan:
which global modules run, and per region what depth — encoding the
spec §7.3 (tier × region_state) matrix."""
from scan_policy import build_scan_plan


def test_quick_runs_globals_and_coverage_no_regional_shasta_no_ai():
    plan = build_scan_plan("quick", {"us-east-1": "active", "eu-west-1": "active"})
    assert plan.run_global_enums is True
    assert plan.global_modules            # global Shasta modules run at Quick
    assert plan.run_ai_pass is False
    for region, rp in plan.per_region.items():
        assert rp.run_enums is True
        assert rp.coverage is True
        assert rp.regional_shasta is False     # Quick skips the 12 regional modules


def test_medium_active_region_is_full_depth():
    plan = build_scan_plan("medium", {"us-east-1": "active"})
    rp = plan.per_region["us-east-1"]
    assert rp.regional_shasta is True
    assert rp.coverage is True
    assert plan.run_ai_pass is True


def test_medium_default_only_region_is_shallow():
    plan = build_scan_plan("medium", {"ap-south-1": "default_only"})
    rp = plan.per_region["ap-south-1"]
    # default-only regions skip the heavy regional Shasta sweep even at Medium
    assert rp.regional_shasta is False
    assert rp.run_enums is True


def test_unknown_region_scanned_as_active():
    plan = build_scan_plan("medium", {"me-central-1": "unknown"})
    rp = plan.per_region["me-central-1"]
    # unknown is scanned conservatively, same depth as active
    assert rp.regional_shasta is True


def test_empty_region_still_present_but_minimal():
    plan = build_scan_plan("medium", {"ap-northeast-3": "empty"})
    rp = plan.per_region["ap-northeast-3"]
    assert rp.regional_shasta is False
    # every region is still in the plan — no silent drop
    assert "ap-northeast-3" in plan.per_region


def test_deep_active_region_adds_capabilities():
    plan = build_scan_plan("deep", {"us-east-1": "active"})
    rp = plan.per_region["us-east-1"]
    assert rp.regional_shasta is True
    assert plan.run_capabilities is True
