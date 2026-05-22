import pytest

from run import build_event


def _env(**overrides):
    base = {
        "SCAN_ID":            "scan-1",
        "TENANT_ID":          "tenant-1",
        "CONN_ID":            "conn-1",
        "PROJECT_IDS":        "proj-a, proj-b",
        "WIF_PROJECT_NUMBER": "123456789",
        "SA_EMAIL":           "ciso-copilot-reader@proj.iam.gserviceaccount.com",
        "WIF_POOL":           "ciso-copilot-pool",
        "WIF_PROVIDER":       "ciso-copilot-aws-provider",
    }
    base.update(overrides)
    return base


def test_build_event_maps_env_vars():
    event = build_event(_env())
    assert event["scan_id"] == "scan-1"
    assert event["tenant_id"] == "tenant-1"
    assert event["conn_id"] == "conn-1"
    assert event["wif_project_number"] == "123456789"
    assert event["sa_email"].startswith("ciso-copilot-reader@")
    assert event["wif_pool"] == "ciso-copilot-pool"
    assert event["wif_provider"] == "ciso-copilot-aws-provider"


def test_build_event_splits_project_ids_and_trims():
    event = build_event(_env())
    assert event["project_ids"] == ["proj-a", "proj-b"]


def test_build_event_defaults_scan_tier_to_quick():
    assert build_event(_env())["scan_tier"] == "quick"


def test_build_event_respects_scan_tier():
    assert build_event(_env(SCAN_TIER="medium"))["scan_tier"] == "medium"


def test_build_event_missing_required_var_raises():
    env = _env()
    del env["SCAN_ID"]
    with pytest.raises(KeyError):
        build_event(env)
