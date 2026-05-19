"""Network enumeration — VPCs, subnets, security groups.

All three call EC2 APIs and are region-bound. Edges:

  aws_account → contains → aws_vpc
  aws_vpc     → contains → aws_subnet
  aws_vpc     → contains → aws_security_group
"""
from __future__ import annotations

from typing import Any

from detectors.base import EdgeEmission, EntityEmission

_DETECTOR_ID      = "shasta_runner.network"
_DETECTOR_VERSION = "0.1.0"


def enumerate_network(
    ec2_client,
    *,
    account_id: str,
    tenant_id:  str,
    region:     str,
) -> dict[str, list]:
    """Enumerate VPCs, subnets, SGs in `region`."""
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []

    # --- VPCs -------------------------------------------------------------
    for vpc in _paginate(ec2_client, "describe_vpcs", "Vpcs"):
        vid = vpc["VpcId"]
        arn = f"arn:aws:ec2:{region}:{account_id}:vpc/{vid}"

        attrs: dict[str, Any] = {
            "service":       "ec2",
            "account":       account_id,
            "region":        region,
            "resource_type": "vpc",
        }
        if "CidrBlock" in vpc:
            attrs["cidr_block"] = vpc["CidrBlock"]
        if "IsDefault" in vpc:
            attrs["is_default"] = vpc["IsDefault"]

        entities.append(EntityEmission(
            tenant_id=tenant_id,
            kind="aws_vpc",
            natural_key=arn,
            display_name=vid,
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
            target_kind="aws_vpc",
            target_natural_key=arn,
            kind="contains",
            attributes={},
            evidence_packet={"version": "0.1", "via": "ec2.describe_vpcs"},
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))

    # --- Subnets ----------------------------------------------------------
    for subnet in _paginate(ec2_client, "describe_subnets", "Subnets"):
        sid = subnet["SubnetId"]
        arn = f"arn:aws:ec2:{region}:{account_id}:subnet/{sid}"
        vpc_id = subnet.get("VpcId")
        vpc_arn = f"arn:aws:ec2:{region}:{account_id}:vpc/{vpc_id}" if vpc_id else None

        attrs = {
            "service":       "ec2",
            "account":       account_id,
            "region":        region,
            "resource_type": "subnet",
        }
        if vpc_id:                attrs["vpc_id"]            = vpc_id
        if "CidrBlock" in subnet: attrs["cidr_block"]        = subnet["CidrBlock"]
        if "AvailabilityZone" in subnet:
            attrs["availability_zone"] = subnet["AvailabilityZone"]

        entities.append(EntityEmission(
            tenant_id=tenant_id,
            kind="aws_subnet",
            natural_key=arn,
            display_name=sid,
            domain="cloud",
            attributes=attrs,
            evidence_packet=None,
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))
        if vpc_arn:
            edges.append(EdgeEmission(
                tenant_id=tenant_id,
                source_kind="aws_vpc",
                source_natural_key=vpc_arn,
                target_kind="aws_subnet",
                target_natural_key=arn,
                kind="contains",
                attributes={},
                evidence_packet={"version": "0.1", "via": "ec2.describe_subnets"},
                detector_id=_DETECTOR_ID,
                detector_version=_DETECTOR_VERSION,
            ))

    # --- Security groups --------------------------------------------------
    for sg in _paginate(ec2_client, "describe_security_groups", "SecurityGroups"):
        sgid = sg["GroupId"]
        arn  = f"arn:aws:ec2:{region}:{account_id}:security-group/{sgid}"
        vpc_id = sg.get("VpcId")
        vpc_arn = f"arn:aws:ec2:{region}:{account_id}:vpc/{vpc_id}" if vpc_id else None

        attrs = {
            "service":       "ec2",
            "account":       account_id,
            "region":        region,
            "resource_type": "security-group",
        }
        if "GroupName" in sg: attrs["group_name"] = sg["GroupName"]
        if vpc_id:            attrs["vpc_id"]     = vpc_id

        entities.append(EntityEmission(
            tenant_id=tenant_id,
            kind="aws_security_group",
            natural_key=arn,
            display_name=sg.get("GroupName") or sgid,
            domain="cloud",
            attributes=attrs,
            evidence_packet=None,
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))
        if vpc_arn:
            edges.append(EdgeEmission(
                tenant_id=tenant_id,
                source_kind="aws_vpc",
                source_natural_key=vpc_arn,
                target_kind="aws_security_group",
                target_natural_key=arn,
                kind="contains",
                attributes={},
                evidence_packet={"version": "0.1",
                                 "via": "ec2.describe_security_groups"},
                detector_id=_DETECTOR_ID,
                detector_version=_DETECTOR_VERSION,
            ))

    return {"entities": entities, "edges": edges}


def _paginate(client, op_name: str, list_key: str):
    if client.can_paginate(op_name):
        for page in client.get_paginator(op_name).paginate():
            yield from page.get(list_key, [])
        return
    resp = getattr(client, op_name)()
    yield from resp.get(list_key, [])
