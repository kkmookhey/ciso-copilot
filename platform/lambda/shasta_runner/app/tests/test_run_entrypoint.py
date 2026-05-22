# app/tests/test_run_entrypoint.py
"""run.build_event reads scan parameters from a dict of env vars into the
event shape main.handler expects. regions is comma-split; scan_tier
defaults to 'quick'; a missing required var raises KeyError."""
import pytest

from run import build_event


def test_build_event_maps_env_to_event():
    env = {
        "SCAN_ID": "s1", "TENANT_ID": "t1", "CONN_ID": "c1",
        "ROLE_ARN": "arn:aws:iam::111:role/CISOCopilotReader",
        "EXTERNAL_ID": "x1", "ACCOUNT_ID": "111111111111",
        "REGIONS": "us-east-1,us-west-2", "SCAN_TIER": "medium",
    }
    event = build_event(env)
    assert event["scan_id"] == "s1"
    assert event["regions"] == ["us-east-1", "us-west-2"]
    assert event["scan_tier"] == "medium"


def test_no_regions_env_omits_regions_so_scanner_discovers():
    env = {
        "SCAN_ID": "s1", "TENANT_ID": "t1", "CONN_ID": "c1",
        "ROLE_ARN": "r", "EXTERNAL_ID": "x", "ACCOUNT_ID": "111111111111",
    }
    event = build_event(env)
    assert event["scan_tier"] == "quick"
    # No REGIONS env -> 'regions' is absent so main.py runs region discovery.
    assert "regions" not in event


def test_missing_required_var_raises():
    with pytest.raises(KeyError):
        build_event({"SCAN_ID": "s1"})
