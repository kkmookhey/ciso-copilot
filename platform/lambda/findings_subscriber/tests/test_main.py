"""End-to-end subscriber tests — full handler orchestration with mocks."""
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock
import pytest


def _sqs_event(body):
    return {"Records": [{"body": json.dumps(body)}]}


def _patch_core(monkeypatch, *, tenant_bot=None, finding=None,
                kill_switch_enabled=True, seen=False):
    """Stub idempotency + kill_switch + DB + admin_session in one shot."""
    from findings_subscriber import idempotency, kill_switch, main as m

    monkeypatch.setattr(idempotency, "seen", lambda **kw: seen)
    mark_seen_calls = []
    monkeypatch.setattr(idempotency, "mark_seen",
                        lambda **kw: mark_seen_calls.append(kw))
    monkeypatch.setattr(kill_switch, "global_enabled",
                        lambda: kill_switch_enabled)

    # Aurora Data API: returns tenant_bot row, then finding row.
    fake_db = MagicMock()
    rows = [tenant_bot, finding]
    fake_db.execute.return_value.fetchone.side_effect = rows
    monkeypatch.setattr("mcp_oauth.session._rds_client", fake_db)
    monkeypatch.setattr("mcp_oauth.admin_session._db", lambda: fake_db, raising=False)

    # MCP admin session
    fake_session = AsyncMock()
    fake_session.call_tool.return_value = MagicMock()
    @contextlib.asynccontextmanager
    async def fake_admin_session(*a, **kw):
        yield fake_session
    monkeypatch.setattr("mcp_oauth.get_admin_session", fake_admin_session)
    # Also patch in the consumer's namespace (the handler imports it).
    monkeypatch.setattr("findings_subscriber.main.get_admin_session",
                        fake_admin_session, raising=False)

    return m, fake_session, mark_seen_calls


def test_happy_path_posts_block_kit_card_and_marks_seen(monkeypatch):
    m, fake_session, mark_seen_calls = _patch_core(
        monkeypatch,
        tenant_bot={"broadcast_channel_id": "C-XYZ",
                     "autonomous_rule_enabled": True,
                     "bot_id": "b-1", "access_token_enc": b"E",
                     "access_data_key_ct": b"DK",
                     "mcp_server_url": "https://mcp.slack.com/mcp",
                     "vendor_workspace_id": "T0",
                     "access_expires_at": None},
        finding={"finding_id": "f-1", "title": "Test",
                 "resource_arn": "arn:aws:s3:::x", "scanner": "aws",
                 "frameworks_list": [], "created_at_epoch": 1717179000,
                 "tenant_id": "t-1", "domain": "cloud", "frameworks": {}},
    )
    monkeypatch.setattr("mcp_oauth.session.decrypt_token", lambda c, dk: "xoxb")

    m.handler(_sqs_event({"tenant_id": "t-1", "finding_id": "f-1",
                          "scan_id": "s-1"}), None)

    fake_session.call_tool.assert_called_once()
    call_args = fake_session.call_tool.call_args
    # The tool name can be either Slack-MCP idiom — accept both.
    tool_name = call_args.args[0] if call_args.args else call_args.kwargs.get("name")
    assert tool_name in ("send_message", "chat_postMessage", "slack_send_message")
    payload = call_args.args[1] if len(call_args.args) >= 2 else call_args.kwargs
    assert payload["channel"] == "C-XYZ"
    assert "blocks" in payload
    assert mark_seen_calls == [
        {"tenant_id": "t-1", "finding_id": "f-1", "scan_id": "s-1"}
    ]


def test_silent_ack_when_already_seen(monkeypatch):
    m, fake_session, _ = _patch_core(monkeypatch, seen=True)
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_kill_switch_off(monkeypatch):
    m, fake_session, _ = _patch_core(monkeypatch, kill_switch_enabled=False)
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_tenant_bot_missing(monkeypatch):
    m, fake_session, _ = _patch_core(monkeypatch, tenant_bot=None)
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_autonomous_rule_disabled(monkeypatch):
    m, fake_session, _ = _patch_core(
        monkeypatch,
        tenant_bot={"broadcast_channel_id": "C", "autonomous_rule_enabled": False,
                     "bot_id": "b", "access_token_enc": b"E",
                     "access_data_key_ct": b"D",
                     "mcp_server_url": "x", "vendor_workspace_id": "T",
                     "access_expires_at": None},
    )
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_no_channel_picked(monkeypatch):
    m, fake_session, _ = _patch_core(
        monkeypatch,
        tenant_bot={"broadcast_channel_id": None, "autonomous_rule_enabled": True,
                     "bot_id": "b", "access_token_enc": b"E",
                     "access_data_key_ct": b"D",
                     "mcp_server_url": "x", "vendor_workspace_id": "T",
                     "access_expires_at": None},
    )
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()


def test_silent_ack_when_finding_disappeared(monkeypatch):
    m, fake_session, _ = _patch_core(
        monkeypatch,
        tenant_bot={"broadcast_channel_id": "C", "autonomous_rule_enabled": True,
                     "bot_id": "b", "access_token_enc": b"E",
                     "access_data_key_ct": b"D",
                     "mcp_server_url": "x", "vendor_workspace_id": "T",
                     "access_expires_at": None},
        finding=None,
    )
    monkeypatch.setattr("mcp_oauth.session.decrypt_token", lambda c, dk: "xoxb")
    m.handler(_sqs_event({"tenant_id": "t", "finding_id": "f", "scan_id": "s"}), None)
    fake_session.call_tool.assert_not_called()
