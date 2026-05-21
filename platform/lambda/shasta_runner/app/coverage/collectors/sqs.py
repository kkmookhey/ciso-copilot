# app/coverage/collectors/sqs.py
"""Collect SQS queues as coverage-engine Resources."""
from __future__ import annotations

from coverage.model import Resource


def collect(client, *, account_id: str, region: str) -> list[Resource]:
    """List every SQS queue in `region` and normalize each to a Resource.

    `client` is a region-bound boto3 SQS client. The queue ARN and all
    attributes come from get_queue_attributes(AttributeNames=['All']).
    """
    resources: list[Resource] = []
    queue_urls = client.list_queues().get("QueueUrls", [])
    for url in queue_urls:
        attrs = client.get_queue_attributes(
            QueueUrl=url, AttributeNames=["All"],
        ).get("Attributes", {})
        arn = attrs.get("QueueArn", url)
        name = url.rsplit("/", 1)[-1]
        resources.append(Resource(
            service="sqs", resource_type="queue",
            arn=arn, name=name, region=region, raw=attrs,
        ))
    return resources
