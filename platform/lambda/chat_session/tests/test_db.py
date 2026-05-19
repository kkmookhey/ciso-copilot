from _db import _resp, _claim_value, _resolve_tenant_id, _resolve_user_context


def test_resp_includes_cors_and_json_body():
    r = _resp(200, {"ok": True})
    assert r["statusCode"] == 200
    assert r["headers"]["access-control-allow-origin"] == "*"
    assert r["headers"]["content-type"] == "application/json"
    assert r["body"] == '{"ok": true}'


def test_claim_value_unwraps_data_api_field():
    assert _claim_value({"stringValue": "abc"}) == "abc"
    assert _claim_value({"isNull": True}) is None
    assert _claim_value({"longValue": 3}) == 3


# --- _resolve_tenant_id injection-override branch (no DB call) ---

def test_resolve_tenant_id_injection_override():
    """When event['_tenant_id'] is set (Function-URL path), return it directly."""
    event = {"_tenant_id": "t-123", "_user_id": "u-456"}
    assert _resolve_tenant_id(event) == "t-123"


def test_resolve_tenant_id_returns_none_when_no_claims_and_no_override():
    """No claims + no injection → None (caller gets 401)."""
    assert _resolve_tenant_id({}) is None


# --- _resolve_user_context injection-override branch (no DB call) ---

def test_resolve_user_context_injection_override():
    """When both _tenant_id and _user_id are injected, returns them without DB."""
    event = {
        "_tenant_id": "t-abc",
        "_user_id":   "u-xyz",
        "_email":     "ciso@example.com",
    }
    email, tenant_id, user_id = _resolve_user_context(event)
    assert tenant_id == "t-abc"
    assert user_id   == "u-xyz"
    assert email     == "ciso@example.com"
