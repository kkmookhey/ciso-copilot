"""run.build_event maps Fargate env vars to the handler event dict."""
import pytest

from run import build_event


def _env(**over):
    base = {
        "SCAN_ID": "scan-1", "TENANT_ID": "tenant-1", "CONN_ID": "conn-1",
        "AZURE_TENANT_ID": "az-tenant-1", "CLIENT_ID": "appid-1",
        "SECRET_ARN": "arn:secret", "SUBSCRIPTION_IDS": "sub-a,sub-b",
    }
    base.update(over)
    return base


def test_build_event_maps_all_fields():
    e = build_event(_env())
    assert e["scan_id"] == "scan-1"
    assert e["tenant_id"] == "tenant-1"
    assert e["conn_id"] == "conn-1"
    assert e["azure_tenant_id"] == "az-tenant-1"
    assert e["client_id"] == "appid-1"
    assert e["secret_arn"] == "arn:secret"
    assert e["subscription_ids"] == ["sub-a", "sub-b"]
    assert e["scan_tier"] == "quick"  # default


def test_build_event_respects_scan_tier():
    assert build_event(_env(SCAN_TIER="medium"))["scan_tier"] == "medium"


def test_build_event_splits_and_strips_subscription_ids():
    e = build_event(_env(SUBSCRIPTION_IDS=" sub-a , sub-b ,"))
    assert e["subscription_ids"] == ["sub-a", "sub-b"]


def test_build_event_missing_required_key_raises():
    env = _env()
    del env["SCAN_ID"]
    with pytest.raises(KeyError):
        build_event(env)
