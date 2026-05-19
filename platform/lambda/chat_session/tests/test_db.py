import _db
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


# --- DB-path tests (monkeypatched _q / execute_statement) ---

def _make_claims_event(identities_json: str | None = None, sub: str = "sub-001") -> dict:
    """Build a minimal API-Gateway-shaped event with Cognito claims."""
    claims: dict = {"sub": sub}
    if identities_json is not None:
        claims["identities"] = identities_json
    return {
        "requestContext": {
            "authorizer": {
                "claims": claims,
            }
        }
    }


def test_resolve_tenant_id_db_path_list_identities(monkeypatch):
    """Normal DB path: identities claim is a JSON array → returns tenant_id from DB row."""
    import json as _json

    fake_records = [[{"stringValue": "tenant-from-db"}]]

    def fake_execute_statement(**kwargs):
        return {"records": fake_records}

    monkeypatch.setattr(_db.rds_data, "execute_statement", fake_execute_statement)

    identities = _json.dumps([{"userId": "fed-user-123", "providerType": "Google"}])
    event = _make_claims_event(identities_json=identities)

    result = _resolve_tenant_id(event)
    assert result == "tenant-from-db"


def test_resolve_tenant_id_db_path_dict_identities(monkeypatch):
    """Edge case: identities claim arrives as a plain dict (not a list)."""
    import json as _json

    fake_records = [[{"stringValue": "tenant-dict-edge"}]]

    def fake_execute_statement(**kwargs):
        return {"records": fake_records}

    monkeypatch.setattr(_db.rds_data, "execute_statement", fake_execute_statement)

    # Single dict, not wrapped in a list — mirrors some IdP implementations
    identities = _json.dumps({"userId": "fed-user-dict", "providerType": "Google"})
    event = _make_claims_event(identities_json=identities)

    result = _resolve_tenant_id(event)
    assert result == "tenant-dict-edge"


def test_resolve_tenant_id_db_path_no_rows(monkeypatch):
    """DB returns no rows → None (user not provisioned)."""
    import json as _json

    def fake_execute_statement(**kwargs):
        return {"records": []}

    monkeypatch.setattr(_db.rds_data, "execute_statement", fake_execute_statement)

    identities = _json.dumps([{"userId": "unknown-user", "providerType": "Google"}])
    event = _make_claims_event(identities_json=identities)

    result = _resolve_tenant_id(event)
    assert result is None


def test_resolve_user_context_db_path(monkeypatch):
    """DB path for _resolve_user_context returns (email, tenant_id, user_id)."""
    import json as _json

    fake_records = [[
        {"stringValue": "alice@example.com"},   # email
        {"stringValue": "tenant-xyz"},           # tenant_id
        {"stringValue": "user-uuid-789"},        # user_id
    ]]

    def fake_execute_statement(**kwargs):
        return {"records": fake_records}

    monkeypatch.setattr(_db.rds_data, "execute_statement", fake_execute_statement)

    identities = _json.dumps([{"userId": "fed-alice", "providerType": "Google"}])
    event = _make_claims_event(identities_json=identities)

    email, tenant_id, user_id = _resolve_user_context(event)
    assert email     == "alice@example.com"
    assert tenant_id == "tenant-xyz"
    assert user_id   == "user-uuid-789"
