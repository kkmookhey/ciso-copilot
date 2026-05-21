# app/tests/test_region_discovery.py
"""Region discovery — classify_regions turns per-region probe results
into the active/skipped/errored breakdown the scanner scopes itself to."""
from region_discovery import RegionDiscovery, classify_regions


def test_active_regions_are_those_with_resources_plus_us_east_1():
    enabled = ["us-east-1", "us-west-2", "eu-west-1"]
    probe = {"us-east-1": False, "us-west-2": True, "eu-west-1": False}
    rd = classify_regions(enabled, probe)
    # us-west-2 has resources; us-east-1 is always included (global anchor).
    assert rd.active_regions == ["us-east-1", "us-west-2"]
    assert rd.skipped_empty == ["eu-west-1"]
    assert rd.errored_regions == []
    assert rd.method == "tagging_api"


def test_errored_region_is_treated_as_active_never_skipped():
    enabled = ["us-east-1", "ap-south-1"]
    probe = {"us-east-1": True, "ap-south-1": None}  # None = sweep errored
    rd = classify_regions(enabled, probe)
    assert "ap-south-1" in rd.active_regions
    assert rd.errored_regions == ["ap-south-1"]
    assert rd.skipped_empty == []


def test_us_east_1_active_even_when_empty_and_never_in_skipped():
    rd = classify_regions(["us-east-1"], {"us-east-1": False})
    assert rd.active_regions == ["us-east-1"]
    assert rd.skipped_empty == []


def test_account_empty_everywhere_still_scans_us_east_1():
    enabled = ["us-east-1", "us-west-2"]
    rd = classify_regions(enabled, {"us-east-1": False, "us-west-2": False})
    assert rd.active_regions == ["us-east-1"]
