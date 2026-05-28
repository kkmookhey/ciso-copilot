import json
import os
from unittest.mock import patch, MagicMock

os.environ.setdefault("DB_CLUSTER_ARN", "test-cluster")
os.environ.setdefault("DB_SECRET_ARN",  "test-secret")
os.environ.setdefault("DB_NAME",        "ciso_copilot_test")
os.environ.setdefault("APNS_PLATFORM_APP_ARN", "arn:aws:sns:us-east-1:111:app/APNS_SANDBOX/test")

from forensic_callback.main import handler


@patch("forensic_callback.main._events")
@patch("forensic_callback.main.push_mod")
@patch("forensic_callback.main._rds")
def test_handler_fires_push_and_deletes_rule(mock_rds, mock_push, mock_events):
    mock_rds.execute_statement.return_value = {
        "records": [[{"stringValue": "tenant-uuid"}]],
    }
    event = {
        "scan_id":          "scan-abc123",
        "target_arn":       "arn:aws:lambda:us-east-1:111:function:prod-ai-router",
        "conversation_id":  "conv-abc",
        "self_delete_rule": "forensic-scan-abc123",
    }
    out = handler(event, None)
    assert out["ok"] is True
    mock_push.notify_tool_completion.assert_called_once()
    kw = mock_push.notify_tool_completion.call_args.kwargs
    assert kw["tenant_id"] == "tenant-uuid"
    assert kw["conversation_id"] == "conv-abc"
    assert "scan-abc123" in kw["payload"]["scan_id"]
    assert kw["payload"]["result"] == "clean"
    mock_events.remove_targets.assert_called_once_with(Rule="forensic-scan-abc123", Ids=["1"])
    mock_events.delete_rule.assert_called_once_with(Name="forensic-scan-abc123")


@patch("forensic_callback.main._events")
@patch("forensic_callback.main.push_mod")
@patch("forensic_callback.main._rds")
def test_handler_missing_conversation_returns_false(mock_rds, mock_push, mock_events):
    mock_rds.execute_statement.return_value = {"records": []}
    event = {
        "scan_id":          "scan-xyz",
        "target_arn":       "arn:aws:lambda:us-east-1:111:function:something",
        "conversation_id":  "conv-missing",
    }
    out = handler(event, None)
    assert out["ok"] is False
    mock_push.notify_tool_completion.assert_not_called()


@patch("forensic_callback.main._events")
@patch("forensic_callback.main.push_mod")
@patch("forensic_callback.main._rds")
def test_handler_rule_cleanup_failure_is_non_fatal(mock_rds, mock_push, mock_events):
    mock_rds.execute_statement.return_value = {
        "records": [[{"stringValue": "tenant-uuid"}]],
    }
    mock_events.remove_targets.side_effect = Exception("rule already gone")
    event = {
        "scan_id":          "scan-xyz",
        "target_arn":       "arn:...",
        "conversation_id":  "conv-abc",
        "self_delete_rule": "forensic-scan-xyz",
    }
    out = handler(event, None)
    assert out["ok"] is True  # push fired; cleanup failure is just logged
