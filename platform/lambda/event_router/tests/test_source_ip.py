"""Router: source_ip column is populated from CloudTrail detail.sourceIPAddress."""
from __future__ import annotations

import os
import sys

# Stub env vars required by main.py's module-level reads before importing main.
os.environ.setdefault("DB_CLUSTER_ARN",    "arn:aws:rds:us-east-1:000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",     "arn:aws:secretsmanager:us-east-1:000:secret:test")
os.environ.setdefault("DB_NAME",           "test")
os.environ.setdefault("RAW_EVENTS_BUCKET", "test-bucket")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


def test_extract_source_ip_from_cloudtrail():
    cloudtrail_event = {
        "detail-type": "AWS API Call via CloudTrail",
        "source":      "aws.ec2",
        "detail": {
            "eventID":         "abc-123",
            "eventName":       "AuthorizeSecurityGroupIngress",
            "sourceIPAddress": "185.220.101.12",
            "userIdentity":    {"arn": "arn:aws:iam::1:user/x"},
            "requestParameters": {"groupId": "sg-abc"},
        },
    }
    assert main._extract_source_ip(cloudtrail_event) == "185.220.101.12"


def test_extract_source_ip_skips_aws_internal():
    """AWS internal calls have sourceIPAddress like 'ec2.amazonaws.com' — not an IP."""
    event = {
        "detail-type": "AWS API Call via CloudTrail",
        "source":      "aws.ec2",
        "detail": {"sourceIPAddress": "ec2.amazonaws.com"},
    }
    assert main._extract_source_ip(event) is None


def test_extract_source_ip_returns_none_for_config_change():
    config_event = {
        "detail-type": "Configuration Item Change Notification",
        "source":      "aws.config",
        "detail":      {"configurationItem": {"resourceType": "AWS::EC2::SecurityGroup"}},
    }
    assert main._extract_source_ip(config_event) is None
