# app/tests/test_collector_sqs.py
"""The SQS collector lists queues and normalizes each into a Resource
carrying the queue's attributes."""
import boto3
from botocore.stub import Stubber

from coverage.collectors.sqs import collect


def test_collect_normalizes_queue_attributes():
    sqs = boto3.client("sqs", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sqs)
    stub.add_response(
        "list_queues",
        {"QueueUrls": ["https://sqs.us-east-1.amazonaws.com/111/q1"]},
    )
    stub.add_response(
        "get_queue_attributes",
        {"Attributes": {
            "QueueArn": "arn:aws:sqs:us-east-1:111:q1",
            "SqsManagedSseEnabled": "true",
        }},
        {"QueueUrl": "https://sqs.us-east-1.amazonaws.com/111/q1",
         "AttributeNames": ["All"]},
    )
    stub.activate()

    resources = collect(sqs, account_id="111", region="us-east-1")

    assert len(resources) == 1
    r = resources[0]
    assert r.service == "sqs"
    assert r.resource_type == "queue"
    assert r.arn == "arn:aws:sqs:us-east-1:111:q1"
    assert r.name == "q1"
    assert r.region == "us-east-1"
    assert r.raw["SqsManagedSseEnabled"] == "true"


def test_collect_handles_no_queues():
    sqs = boto3.client("sqs", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sqs)
    stub.add_response("list_queues", {})
    stub.activate()

    assert collect(sqs, account_id="111", region="us-east-1") == []
