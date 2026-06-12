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


def test_get_includes_message_id(monkeypatch):
    """get() must return message id so the frontend can PATCH individual messages."""
    import json

    calls = []

    def fake_q(sql, params=None):
        calls.append(sql)
        if "FROM conversations" in sql:
            return [[{"stringValue": "conv-uuid"}, {"stringValue": "Title"}]]
        if "FROM conversation_messages" in sql:
            return [[
                {"stringValue": "msg-uuid-1"},
                {"stringValue": "assistant"},
                {"stringValue": json.dumps({"text": "hello"})},
                {"stringValue": "2026-01-01T00:00:00"},
            ]]
        return []

    monkeypatch.setattr(C, "_q", fake_q)
    result = C.get("tenant-1", "conv-uuid")
    assert result is not None
    assert len(result["messages"]) == 1
    msg = result["messages"][0]
    assert msg["id"] == "msg-uuid-1"
    assert msg["role"] == "assistant"
    assert msg["content"] == {"text": "hello"}
    # Verify SELECT includes id column
    msg_sql = next(s for s in calls if "conversation_messages" in s)
    assert "id::text" in msg_sql


def test_patch_title_returns_false_when_no_row(monkeypatch):
    monkeypatch.setattr(C, "_q", lambda sql, params=None: [])
    assert C.patch_title("tenant-1", "nonexistent-id", "New Title") is False


def test_soft_delete_returns_false_when_no_row(monkeypatch):
    monkeypatch.setattr(C, "_q", lambda sql, params=None: [])
    assert C.soft_delete("tenant-1", "nonexistent-id") is False


def test_patch_title_if_default_returns_true_when_row_updated(monkeypatch):
    captured = {}

    def fake_q(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [[{"stringValue": "conv-uuid"}]]

    monkeypatch.setattr(C, "_q", fake_q)
    assert C.patch_title_if_default("tenant-1", "conv-uuid", "Auto Title") is True
    assert "UPDATE conversations" in captured["sql"]
    assert "title = 'New conversation'" in captured["sql"]
    assert "tenant_id = :tenant_id::uuid" in captured["sql"]
    assert "id = :id::uuid" in captured["sql"]
    assert captured["params"]["title"] == "Auto Title"
    assert captured["params"]["tenant_id"] == "tenant-1"
    assert captured["params"]["id"] == "conv-uuid"


def test_patch_title_if_default_returns_false_when_no_row(monkeypatch):
    """No row returned -> title was already custom or wrong tenant."""
    monkeypatch.setattr(C, "_q", lambda sql, params=None: [])
    assert C.patch_title_if_default("tenant-1", "conv-uuid", "Auto Title") is False


def test_patch_title_if_default_sql_returns_id(monkeypatch):
    """RETURNING id::text is what we use to detect 'a row was updated'."""
    captured = {}
    monkeypatch.setattr(C, "_q",
                        lambda sql, params=None: captured.update(sql=sql) or [])
    C.patch_title_if_default("t", "c", "T")
    assert "RETURNING id::text" in captured["sql"]
