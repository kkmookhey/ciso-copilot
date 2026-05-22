# app/tests/test_collector_ecr.py
"""The ECR collector lists private repositories and normalizes each into
a Resource carrying the repository description."""
import boto3
from botocore.stub import Stubber

from coverage.collectors.ecr import collect


def test_collect_normalizes_repositories():
    ecr = boto3.client("ecr", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ecr)
    stub.add_response(
        "describe_repositories",
        {"repositories": [{
            "repositoryArn": "arn:aws:ecr:us-east-1:111:repository/app",
            "repositoryName": "app",
            "imageTagMutability": "IMMUTABLE",
            "imageScanningConfiguration": {"scanOnPush": True},
        }]},
    )
    stub.activate()

    resources = collect(ecr, account_id="111", region="us-east-1")

    assert len(resources) == 1
    r = resources[0]
    assert r.service == "ecr"
    assert r.resource_type == "repository"
    assert r.arn == "arn:aws:ecr:us-east-1:111:repository/app"
    assert r.name == "app"
    assert r.raw["imageTagMutability"] == "IMMUTABLE"


def test_collect_paginates():
    ecr = boto3.client("ecr", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ecr)
    stub.add_response("describe_repositories", {
        "repositories": [{"repositoryArn": "arn:...:repository/r1",
                          "repositoryName": "r1"}],
        "nextToken": "tok",
    })
    stub.add_response("describe_repositories", {
        "repositories": [{"repositoryArn": "arn:...:repository/r2",
                          "repositoryName": "r2"}],
    }, {"nextToken": "tok"})
    stub.activate()

    resources = collect(ecr, account_id="111", region="us-east-1")
    assert sorted(r.name for r in resources) == ["r1", "r2"]
