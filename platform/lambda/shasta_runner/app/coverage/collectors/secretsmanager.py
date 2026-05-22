# app/coverage/collectors/secretsmanager.py
"""Collect Secrets Manager secrets as coverage-engine Resources."""
from __future__ import annotations

from coverage.model import Resource


def collect(client, *, account_id: str, region: str) -> list[Resource]:
    """List every Secrets Manager secret in `region`.

    `client` is a region-bound boto3 secretsmanager client. list_secrets
    returns RotationEnabled and KmsKeyId inline on each SecretListEntry.
    """
    resources: list[Resource] = []
    paginator = client.get_paginator("list_secrets")
    for page in paginator.paginate():
        for entry in page.get("SecretList", []):
            arn = entry.get("ARN", "")
            resources.append(Resource(
                service="secretsmanager", resource_type="secret",
                arn=arn, name=entry.get("Name", arn),
                region=region, raw=entry,
            ))
    return resources
