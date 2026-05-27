"""Sample EventBridge events for tests."""
from __future__ import annotations
import pytest


@pytest.fixture
def cloudtrail_sg_open_event() -> dict:
    """A real-shape CloudTrail event for AuthorizeSecurityGroupIngress :22 to 0.0.0.0/0."""
    return {
        "version":     "0",
        "id":          "ebr-event-abc123",
        "detail-type": "AWS API Call via CloudTrail",
        "source":      "aws.cloudtrail",
        "account":     "999999999999",
        "time":        "2026-05-25T18:42:10Z",
        "region":      "us-east-1",
        "detail": {
            "eventID":          "ct-eventid-7f3a9c",
            "eventName":        "AuthorizeSecurityGroupIngress",
            "eventSource":      "ec2.amazonaws.com",
            "userIdentity":     {"arn": "arn:aws:iam::999999999999:user/test-user"},
            "requestParameters": {
                "groupId": "sg-0abc123def",
                "ipPermissions": {"items": [{"ipProtocol": "tcp", "fromPort": 22, "toPort": 22,
                                             "ipRanges": {"items": [{"cidrIp": "0.0.0.0/0"}]}}]},
            },
            "resources":       [{"ARN": "arn:aws:ec2:us-east-1:999999999999:security-group/sg-0abc123def"}],
        },
    }


@pytest.fixture
def config_item_change_event() -> dict:
    """A real-shape AWS Config item change event."""
    return {
        "version":     "0",
        "id":          "ebr-event-xyz789",
        "detail-type": "Configuration Item Change Notification",
        "source":      "aws.config",
        "account":     "999999999999",
        "time":        "2026-05-25T18:43:15Z",
        "region":      "us-east-1",
        "detail": {
            "configurationItem": {
                "configurationItemCaptureTime": "2026-05-25T18:43:14.123Z",
                "configurationItemStatus":     "OK",
                "configurationStateId":        "1716658994123",
                "resourceType":                "AWS::EC2::SecurityGroup",
                "resourceId":                  "sg-0abc123def",
                "ARN":                         "arn:aws:ec2:us-east-1:999999999999:security-group/sg-0abc123def",
                "configuration":               {"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                                                   "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]},
            },
            "configurationItemDiff": {
                "changeType": "UPDATE",
                "changedProperties": {
                    "Configuration.IpPermissions.0": {
                        "previousValue": [],
                        "updatedValue":  [{"fromPort": 22, "toPort": 22,
                                          "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}],
                        "changeType":    "UPDATE",
                    },
                },
            },
        },
    }
