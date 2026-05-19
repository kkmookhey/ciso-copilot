# platform/lambda/chat_session/tests/test_conversations.py
import conversations as C


def test_create_conversation_returns_uuid(monkeypatch):
    captured = {}

    def fake_q(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(C, "_q", fake_q)
    out = C.create("tenant-1", "user-1")
    assert "conversation_id" in out
    assert len(out["conversation_id"]) == 36
    assert "INSERT INTO conversations" in captured["sql"]
    assert captured["params"]["tenant_id"] == "tenant-1"


def test_list_filters_deleted(monkeypatch):
    captured = {}
    monkeypatch.setattr(C, "_q", lambda sql, params=None: captured.update(sql=sql) or [])
    C.list_for("tenant-1", "user-1")
    assert "deleted_at IS NULL" in captured["sql"]


def test_get_returns_none_for_wrong_tenant(monkeypatch):
    """get() must return None when the conversation is not in the caller's tenant."""
    monkeypatch.setattr(C, "_q", lambda sql, params=None: [])
    result = C.get("other-tenant", "some-conversation-id")
    assert result is None


def test_patch_title_returns_false_when_no_row(monkeypatch):
    monkeypatch.setattr(C, "_q", lambda sql, params=None: [])
    assert C.patch_title("tenant-1", "nonexistent-id", "New Title") is False


def test_soft_delete_returns_false_when_no_row(monkeypatch):
    monkeypatch.setattr(C, "_q", lambda sql, params=None: [])
    assert C.soft_delete("tenant-1", "nonexistent-id") is False
