"""Push rule evaluation + rate limit + SNS Mobile Push call."""
import push


def test_should_push_critical_always_true():
    assert push.should_push("critical", current_hour_count=0) is True


def test_should_push_high_when_under_threshold():
    assert push.should_push("high", current_hour_count=0) is True


def test_should_push_medium_skipped_by_default():
    assert push.should_push("medium", current_hour_count=0) is False


def test_should_push_high_skipped_when_over_rate_limit():
    assert push.should_push("high", current_hour_count=10) is False


def test_should_push_critical_skipped_when_over_rate_limit():
    # criticals bypass the cap (operational safety — never silently drop a critical)
    assert push.should_push("critical", current_hour_count=999) is True


def test_format_push_body_drift():
    body = push.format_push_body(
        kind="drift", severity="high", title="AuthorizeSecurityGroupIngress",
        resource_arn="arn:aws:ec2:us-east-1:123:security-group/sg-abc",
        actor="arn:aws:iam::123:user/x",
    )
    assert "drift" in body.lower()
    assert "sg-abc" in body
    assert "user/x" in body


def test_send_push_calls_sns(monkeypatch):
    calls = []
    class FakeSns:
        def publish(self, **kw):
            calls.append(kw)
            return {"MessageId": "m-1"}
        def create_platform_endpoint(self, **kw):
            return {"EndpointArn": "arn:aws:sns:us-east-1:123:endpoint/APNS/test/abc"}
    monkeypatch.setattr(push, "sns", FakeSns())
    push.send_push(
        device_tokens=["device-token-aaa"],
        platform_app_arn="arn:aws:sns:us-east-1:123:app/APNS/test",
        body="hi",
    )
    assert len(calls) == 1
    assert calls[0]["TargetArn"].startswith("arn:aws:sns:")
