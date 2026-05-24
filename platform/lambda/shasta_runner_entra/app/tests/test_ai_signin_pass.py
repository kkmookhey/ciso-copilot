"""Unit tests for ai_signin_pass.

Pure helpers (load_catalog, match_app, signin_to_params) are tested
against fixture dicts. run_ai_signin_pass is not unit-tested here —
it's exercised via deployed smoke (Task 7).
"""
from __future__ import annotations

import json
import os
import tempfile

from ai_signin_pass import load_catalog, match_app, signin_to_params


_FIXTURE_CATALOG = {
    "OpenAI": {
        "match": {
            "app_display_names": ["OpenAI", "ChatGPT"],
            "app_ids": ["00000000-aaaa-bbbb-cccc-000000000001"]
        },
        "default_severity": "high",
        "tier_inference": {"enterprise": "corp", "teams": "corp"}
    },
    "GitHub Copilot": {
        "match": {"app_display_names": ["GitHub Copilot"], "app_ids": []},
        "default_severity": "low",
        "tier_inference": None
    }
}


def test_load_catalog_parses_json(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(_FIXTURE_CATALOG))
    assert load_catalog(str(p)) == _FIXTURE_CATALOG


def test_match_app_by_display_name_personal_tier():
    event = {"appDisplayName": "ChatGPT", "appId": "x"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name == "OpenAI"
    assert tier == "unknown"          # "ChatGPT" has no tier keyword in it
    assert sev == "high"


def test_match_app_by_display_name_enterprise_inference():
    event = {"appDisplayName": "ChatGPT Enterprise", "appId": "x"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name == "OpenAI"
    assert tier == "corp"
    # Catalog default_severity stays; the orchestrator decides whether
    # to override severity for corp tier — see signin_to_params.


def test_match_app_by_app_id_when_display_name_missing():
    event = {"appDisplayName": "", "appId": "00000000-aaaa-bbbb-cccc-000000000001"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name == "OpenAI"


def test_match_app_returns_none_for_non_ai_app():
    event = {"appDisplayName": "Microsoft Teams", "appId": "y"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name is None
    assert tier is None
    assert sev is None


def test_match_app_handles_missing_tier_inference():
    event = {"appDisplayName": "GitHub Copilot", "appId": "z"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name == "GitHub Copilot"
    assert tier == "unknown"          # no tier_inference rules → unknown
    assert sev == "low"


def test_signin_to_params_personal_tier_emits_fail_high():
    event = {
        "appDisplayName": "ChatGPT",
        "appId": "00000000-aaaa-bbbb-cccc-000000000001",
        "userPrincipalName": "alice@acme.com",
        "createdDateTime": "2026-05-23T10:00:00Z",
        "id": "signin-evt-1",
    }
    params = signin_to_params(
        event, name="OpenAI", tier="unknown", catalog_severity="high",
        tenant_id="TEN", conn_id="CONN", scan_id="SCAN",
        entra_tenant_id="ETEN",
    )
    by_name = {p["name"]: p["value"]["stringValue"] for p in params}
    assert by_name["check_id"] == "ai_signin_unknown_tier"
    assert by_name["severity"] == "high"
    assert by_name["status"] == "fail"
    assert by_name["domain"] == "identity"
    assert by_name["resource_type"] == "ai_signin"
    assert by_name["region"] == "ETEN"


def test_signin_to_params_corp_tier_emits_pass_low():
    event = {
        "appDisplayName": "ChatGPT Enterprise",
        "appId": "x",
        "userPrincipalName": "bob@acme.com",
        "createdDateTime": "2026-05-23T10:00:00Z",
        "id": "signin-evt-2",
    }
    params = signin_to_params(
        event, name="OpenAI", tier="corp", catalog_severity="high",
        tenant_id="TEN", conn_id="CONN", scan_id="SCAN",
        entra_tenant_id="ETEN",
    )
    by_name = {p["name"]: p["value"]["stringValue"] for p in params}
    assert by_name["check_id"] == "ai_signin_corp_tier"
    assert by_name["severity"] == "low"     # corp tier downgrades severity
    assert by_name["status"] == "pass"      # corp tier is OK posture-wise


def test_signin_to_params_includes_entra_upn_in_evidence():
    """evidence_packet must carry entra_upn for the /ai per-person view to populate."""
    event = {
        "appDisplayName": "ChatGPT", "appId": "x",
        "userPrincipalName": "carol@acme.com",
        "createdDateTime": "2026-05-23T10:00:00Z", "id": "evt",
    }
    params = signin_to_params(
        event, name="OpenAI", tier="unknown", catalog_severity="high",
        tenant_id="TEN", conn_id="CONN", scan_id="SCAN", entra_tenant_id="ETEN",
    )
    by_name = {p["name"]: p["value"]["stringValue"] for p in params}
    ev = json.loads(by_name["evidence_packet"])
    assert ev["entra_upn"] == "carol@acme.com"
    assert ev["is_ai"] == "true"
    assert ev["app"] == "OpenAI"


def test_fetch_signins_returns_premium_required_on_specific_403():
    """When Graph returns the licensing-403, _fetch_signins signals it."""
    from ai_signin_pass import _fetch_signins

    class FakeError(Exception):
        def __init__(self):
            self.error = type("E", (), {"code": "Authentication_RequestFromNonPremiumTenantOrB2CTenant"})()
            self.response_status_code = 403

    class FakeGraph:
        class _Audit:
            class _SignIns:
                def get(self, request_configuration=None):
                    raise FakeError()
            sign_ins = _SignIns()
        audit_logs = _Audit()

    events, premium_required = _fetch_signins(FakeGraph(), last_scan_at=None)
    assert events == []
    assert premium_required is True


def test_fetch_signins_does_not_flag_other_403s():
    """Other 403s (revoked consent, missing scope) do NOT set premium_required."""
    from ai_signin_pass import _fetch_signins

    class FakeError(Exception):
        def __init__(self):
            self.error = type("E", (), {"code": "Authorization_RequestDenied"})()
            self.response_status_code = 403

    class FakeGraph:
        class _Audit:
            class _SignIns:
                def get(self, request_configuration=None):
                    raise FakeError()
            sign_ins = _SignIns()
        audit_logs = _Audit()

    events, premium_required = _fetch_signins(FakeGraph(), last_scan_at=None)
    assert events == []
    assert premium_required is False


def test_run_ai_signin_pass_returns_tuple():
    """Top-level orchestrator returns (param_lists, premium_required)."""
    from ai_signin_pass import run_ai_signin_pass

    class FakeError(Exception):
        def __init__(self):
            self.error = type("E", (), {"code": "Authentication_RequestFromNonPremiumTenantOrB2CTenant"})()
            self.response_status_code = 403

    class FakeGraph:
        class _Audit:
            class _SignIns:
                def get(self, request_configuration=None):
                    raise FakeError()
            sign_ins = _SignIns()
        audit_logs = _Audit()

    params, premium_required = run_ai_signin_pass(
        graph_client=FakeGraph(),
        tenant_id="TEN", conn_id="CONN", scan_id="SCAN",
        entra_tenant_id="ETEN",
    )
    assert params == []
    assert premium_required is True
