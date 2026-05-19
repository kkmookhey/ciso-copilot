"""Compute enumeration — EC2 instances + Lambda functions.

Both clients are *region-bound* (the caller passes pre-built boto3 clients
already targeted at the desired region; the runner loops regions itself).

EC2 instance → IAM role edge:
  EC2 surfaces the instance profile ARN (`IamInstanceProfile.Arn`), NOT the
  role ARN directly. The mapping from instance-profile → role requires an
  extra `iam.get_instance_profile` call. To keep this enum cheap and self-
  contained, we emit the edge using the instance-profile ARN as the
  natural_key of the `aws_iam_role` target. `unified_writer` will create a
  stub entity (`{_stub: true}`) for it; the cloud team can replace it with
  a real-role lookup later. This is intentional and called out in the plan.

Lambda function → IAM role edge:
  Lambda's `Role` field IS the role ARN. Emit the edge directly with that
  ARN. The role entity itself is populated by `enumerate_iam`; if the
  role is in a different account (cross-account exec role) the writer
  stubs it.
"""
from __future__ import annotations

from typing import Any

from detectors.base import EdgeEmission, EntityEmission

_DETECTOR_ID      = "shasta_runner.compute"
_DETECTOR_VERSION = "0.1.0"


def enumerate_compute(
    ec2_client,
    lambda_client,
    *,
    account_id: str,
    tenant_id:  str,
    region:     str,
) -> dict[str, list]:
    """Enumerate EC2 instances + Lambda functions in `region`."""
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []

    # --- EC2 instances ----------------------------------------------------
    for instance in _ec2_instances(ec2_client):
        iid = instance["InstanceId"]
        arn = f"arn:aws:ec2:{region}:{account_id}:instance/{iid}"

        attrs: dict[str, Any] = {
            "service":       "ec2",
            "account":       account_id,
            "region":        region,
            "resource_type": "instance",
        }
        if "InstanceType" in instance:
            attrs["instance_type"] = instance["InstanceType"]
        if "State" in instance:
            attrs["state"] = instance["State"].get("Name")
        if instance.get("VpcId"):
            attrs["vpc_id"] = instance["VpcId"]

        entities.append(EntityEmission(
            tenant_id=tenant_id,
            kind="aws_ec2_instance",
            natural_key=arn,
            display_name=iid,
            domain="cloud",
            attributes=attrs,
            evidence_packet=None,
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))
        edges.append(EdgeEmission(
            tenant_id=tenant_id,
            source_kind="aws_account",
            source_natural_key=account_id,
            target_kind="aws_ec2_instance",
            target_natural_key=arn,
            kind="contains",
            attributes={},
            evidence_packet={"version": "0.1", "via": "ec2.describe_instances"},
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))

        prof = (instance.get("IamInstanceProfile") or {}).get("Arn")
        if prof:
            # Use the profile ARN as the role natural_key — best we can do
            # without an extra API call. unified_writer stubs the target.
            edges.append(EdgeEmission(
                tenant_id=tenant_id,
                source_kind="aws_ec2_instance",
                source_natural_key=arn,
                target_kind="aws_iam_role",
                target_natural_key=prof,
                kind="assumes",
                attributes={"via": "iam_instance_profile_arn"},
                evidence_packet={
                    "version": "0.1",
                    "via": "ec2.describe_instances",
                    "note": "natural_key is instance-profile ARN; "
                            "role-name lookup deferred",
                },
                detector_id=_DETECTOR_ID,
                detector_version=_DETECTOR_VERSION,
            ))

    # --- Lambda functions -------------------------------------------------
    for fn in _lambda_functions(lambda_client):
        arn  = fn["FunctionArn"]
        name = fn["FunctionName"]

        attrs = {
            "service":       "lambda",
            "account":       account_id,
            "region":        region,
            "resource_type": "function",
        }
        if "Runtime" in fn:    attrs["runtime"] = fn["Runtime"]
        if "Handler" in fn:    attrs["handler"] = fn["Handler"]
        if "MemorySize" in fn: attrs["memory_size"] = fn["MemorySize"]

        entities.append(EntityEmission(
            tenant_id=tenant_id,
            kind="aws_lambda_function",
            natural_key=arn,
            display_name=name,
            domain="cloud",
            attributes=attrs,
            evidence_packet=None,
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))
        edges.append(EdgeEmission(
            tenant_id=tenant_id,
            source_kind="aws_account",
            source_natural_key=account_id,
            target_kind="aws_lambda_function",
            target_natural_key=arn,
            kind="contains",
            attributes={},
            evidence_packet={"version": "0.1", "via": "lambda.list_functions"},
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))

        role_arn = fn.get("Role")
        if role_arn:
            edges.append(EdgeEmission(
                tenant_id=tenant_id,
                source_kind="aws_lambda_function",
                source_natural_key=arn,
                target_kind="aws_iam_role",
                target_natural_key=role_arn,
                kind="assumes",
                attributes={},
                evidence_packet={"version": "0.1",
                                 "via": "lambda.list_functions.Role"},
                detector_id=_DETECTOR_ID,
                detector_version=_DETECTOR_VERSION,
            ))

    return {"entities": entities, "edges": edges}


def _ec2_instances(ec2_client):
    if ec2_client.can_paginate("describe_instances"):
        for page in ec2_client.get_paginator("describe_instances").paginate():
            for reservation in page.get("Reservations", []):
                yield from reservation.get("Instances", [])
        return
    resp = ec2_client.describe_instances()
    for reservation in resp.get("Reservations", []):
        yield from reservation.get("Instances", [])


def _lambda_functions(lambda_client):
    if lambda_client.can_paginate("list_functions"):
        for page in lambda_client.get_paginator("list_functions").paginate():
            yield from page.get("Functions", [])
        return
    resp = lambda_client.list_functions()
    yield from resp.get("Functions", [])
