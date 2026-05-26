import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def sample_sqs_event() -> dict:
    return {
        "Records": [{
            "messageId": "msg-1",
            "body": '{"event_id": "11111111-1111-1111-1111-111111111111", "tenant_id": "22222222-2222-2222-2222-222222222222"}',
        }]
    }


@pytest.fixture
def sample_event_row() -> dict:
    return {
        "event_id":        "11111111-1111-1111-1111-111111111111",
        "tenant_id":       "22222222-2222-2222-2222-222222222222",
        "source":          "aws.config",
        "kind":            "drift",
        "severity":        "high",
        "title":           "AuthorizeSecurityGroupIngress",
        "actor":           "arn:aws:iam::470226123496:user/test-user",
        "resource_arn":    "arn:aws:ec2:us-east-1:470226123496:security-group/sg-abc",
        "fired_at":        "2026-05-25T18:42:10Z",
        "before_state":    {},
        "after_state":     {"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                                "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]},
    }
