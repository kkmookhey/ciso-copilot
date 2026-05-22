# app/tests/test_collector_secretsmanager.py
"""The Secrets Manager collector lists secrets and normalizes each into
a Resource carrying the SecretListEntry fields."""
import boto3
from botocore.stub import Stubber

from coverage.collectors.secretsmanager import collect


def test_collect_normalizes_secrets():
    sm = boto3.client("secretsmanager", region_name="us-east-1",
                      aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sm)
    stub.add_response(
        "list_secrets",
        {"SecretList": [{
            "ARN": "arn:aws:secretsmanager:us-east-1:111:secret:db-x",
            "Name": "db-x",
            "RotationEnabled": True,
            "KmsKeyId": "arn:aws:kms:us-east-1:111:key/abc",
        }]},
    )
    stub.activate()

    resources = collect(sm, account_id="111", region="us-east-1")

    assert len(resources) == 1
    r = resources[0]
    assert r.service == "secretsmanager"
    assert r.resource_type == "secret"
    assert r.arn == "arn:aws:secretsmanager:us-east-1:111:secret:db-x"
    assert r.name == "db-x"
    assert r.raw["RotationEnabled"] is True


def test_collect_paginates():
    sm = boto3.client("secretsmanager", region_name="us-east-1",
                      aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sm)
    stub.add_response("list_secrets", {
        "SecretList": [{"ARN": "arn:a:b:c:111:secret:s1", "Name": "s1"}],
        "NextToken": "tok",
    })
    stub.add_response("list_secrets", {
        "SecretList": [{"ARN": "arn:a:b:c:111:secret:s2", "Name": "s2"}],
    }, {"NextToken": "tok"})
    stub.activate()

    resources = collect(sm, account_id="111", region="us-east-1")
    assert sorted(r.name for r in resources) == ["s1", "s2"]
