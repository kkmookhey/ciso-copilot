# app/coverage/collectors/ecr.py
"""Collect ECR private repositories as coverage-engine Resources."""
from __future__ import annotations

from coverage.model import Resource


def collect(client, *, account_id: str, region: str) -> list[Resource]:
    """List every private ECR repository in `region`.

    `client` is a region-bound boto3 ecr client. describe_repositories
    returns imageTagMutability and imageScanningConfiguration inline.
    """
    resources: list[Resource] = []
    paginator = client.get_paginator("describe_repositories")
    for page in paginator.paginate():
        for repo in page.get("repositories", []):
            arn = repo.get("repositoryArn", "")
            resources.append(Resource(
                service="ecr", resource_type="repository",
                arn=arn, name=repo.get("repositoryName", arn),
                region=region, raw=repo,
            ))
    return resources
