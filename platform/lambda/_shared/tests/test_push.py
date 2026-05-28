# platform/lambda/_shared/tests/test_push.py
import json
from unittest.mock import patch, MagicMock

from _shared.push import (
    should_push, format_push_body, send_push, send_push_with_payload,
    tokens_for_tenant, notify_tool_completion,
    PUSH_THRESHOLD, PUSH_RATE_LIMIT_HOUR,
)


def test_critical_always_pushes():
    assert should_push("critical", current_hour_count=99) is True


def test_below_threshold_blocks():
    assert should_push("medium", current_hour_count=0) is False


def test_high_within_limit_allows():
    assert should_push("high", current_hour_count=5) is True


def test_high_over_limit_blocks():
    assert should_push("high", current_hour_count=10) is False


def test_format_push_body_basic():
    body = format_push_body(kind="drift", severity="high",
                            title="bucket policy changed",
                            resource_arn="arn:aws:s3:::my-bucket",
                            actor="arn:aws:iam::1:user/mike")
    assert "drift" in body and "high" in body and "my-bucket" in body
    assert "user/mike" in body


@patch("_shared.push.sns")
def test_send_push_with_payload_includes_extra(mock_sns):
    mock_sns.create_platform_endpoint.return_value = {"EndpointArn": "arn:..."}
    send_push_with_payload(
        device_tokens=["t1"], platform_app_arn="app-arn",
        body="Test", payload={"finding_id": "f-1"},
    )
    assert mock_sns.publish.called
    msg = mock_sns.publish.call_args.kwargs["Message"]
    inner = json.loads(json.loads(msg)["APNS_SANDBOX"])
    assert inner["finding_id"] == "f-1"
    assert inner["aps"]["alert"] == "Test"


@patch("_shared.push.sns")
def test_send_push_legacy_body_only(mock_sns):
    mock_sns.create_platform_endpoint.return_value = {"EndpointArn": "arn:..."}
    send_push(device_tokens=["t1"], platform_app_arn="app-arn", body="Plain")
    assert mock_sns.publish.called
    msg = mock_sns.publish.call_args.kwargs["Message"]
    inner = json.loads(json.loads(msg)["APNS_SANDBOX"])
    assert inner["aps"]["alert"] == "Plain"


def test_tokens_for_tenant_extracts_strings():
    fake_rds = MagicMock()
    fake_rds.execute_statement.return_value = {
        "records": [
            [{"stringValue": "token-1"}],
            [{"stringValue": "token-2"}],
            [{"stringValue": ""}],   # filtered out
        ]
    }
    out = tokens_for_tenant(
        "tenant-uuid",
        rds=fake_rds, db_cluster_arn="c", db_secret_arn="s", db_name="n",
    )
    assert out == ["token-1", "token-2"]


@patch("_shared.push.sns")
def test_notify_tool_completion_carries_conversation_id(mock_sns):
    mock_sns.create_platform_endpoint.return_value = {"EndpointArn": "arn:..."}
    fake_rds = MagicMock()
    fake_rds.execute_statement.return_value = {"records": [[{"stringValue": "t1"}]]}
    notify_tool_completion(
        tenant_id="tenant-uuid", conversation_id="conv-abc",
        body="Done.", payload={"scan_id": "s-1"},
        rds=fake_rds, db_cluster_arn="c", db_secret_arn="s", db_name="n",
        platform_app_arn="app-arn",
    )
    msg = mock_sns.publish.call_args.kwargs["Message"]
    inner = json.loads(json.loads(msg)["APNS_SANDBOX"])
    assert inner["conversation_id"] == "conv-abc"
    assert inner["scan_id"] == "s-1"
