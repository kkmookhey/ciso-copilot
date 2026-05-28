# platform/lambda/tools/tests/test_slack_dm.py
from unittest.mock import patch
from tools.slack_dm import handle


@patch("tools.slack_dm._mcp_client")
def test_resolves_user_and_sends_dm(mock_client):
    mock_client.call.side_effect = [
        # 1st call: slack_lookup_user
        {"user": {"id": "U123ABC"}},
        # 2nd call: slack_post_message
        {"ts": "1717030000.001", "channel": "D123"},
    ]
    result = handle(
        {"user_lookup": "sarah.chen@acme.io", "message": "Heads up"},
        {"sub": "test-user"},
    )
    assert result["ts"] == "1717030000.001"
    assert result["channel"] == "D123"
    assert "speakable" in result
    # Spec: the speakable mentions the recipient (name or local-part) or "Slack".
    assert "sarah.chen" in result["speakable"].lower() or "slack" in result["speakable"].lower()


@patch("tools.slack_dm._mcp_client")
def test_user_not_found(mock_client):
    mock_client.call.return_value = {"error": "users_not_found"}
    result = handle(
        {"user_lookup": "ghost@acme.io", "message": "Hi"},
        {"sub": "test-user"},
    )
    assert result["sent"] is False
    assert result["reason"] == "user_not_found"
