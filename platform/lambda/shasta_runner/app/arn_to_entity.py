"""Parse an AWS ARN into an entity-emission shape (kind + natural_key +
display_name + attributes).

Used by `shasta_runner/app/main.py` to derive entity rows from the
`resource_id` fields Shasta already populates on every finding. The
returned dict has the same keys EntityEmission would consume; the caller
wraps it in an EntityEmission with tenant_id, evidence packet, etc.

Returns None for ARNs we don't have a kind mapping for yet — caller
should treat that as "no entity, just keep the finding".
"""
from __future__ import annotations

import re

# arn:aws:<service>:<region>:<account>:<resource>
# Resource can be `<type>/<id>`, `<type>:<id>`, or just `<id>` (e.g. S3 buckets).
_ARN_RE = re.compile(r"^arn:aws:([^:]+):([^:]*):([^:]*):(.+)$")

# (service, resource_type) → entity kind.
# Use "*" as a wildcard resource_type for services that have a single kind.
_KIND_MAP = {
    ("s3",     "*"):              "aws_s3_bucket",
    ("iam",    "role"):           "aws_iam_role",
    ("iam",    "user"):           "aws_iam_user",
    ("ec2",    "instance"):       "aws_ec2_instance",
    ("ec2",    "vpc"):            "aws_vpc",
    ("ec2",    "subnet"):         "aws_subnet",
    ("ec2",    "security-group"): "aws_security_group",
    ("lambda", "function"):       "aws_lambda_function",
    ("eks",    "cluster"):        "aws_eks_cluster",
}


def parse_arn(arn: str) -> dict | None:
    """Return {kind, natural_key, display_name, attributes} or None."""
    if not arn or not isinstance(arn, str):
        return None
    m = _ARN_RE.match(arn)
    if not m:
        return None
    service, region, account, resource = m.groups()

    # Split the resource into (type, id). S3 buckets have just an id.
    if "/" in resource:
        resource_type, resource_id = resource.split("/", 1)
    elif ":" in resource:
        resource_type, resource_id = resource.split(":", 1)
    else:
        resource_type, resource_id = "*", resource

    kind = _KIND_MAP.get((service, resource_type)) or _KIND_MAP.get((service, "*"))
    if kind is None:
        return None

    attrs: dict = {"service": service}
    if region:
        attrs["region"] = region
    if account:
        attrs["account"] = account
    if resource_type and resource_type != "*":
        attrs["resource_type"] = resource_type

    return {
        "kind":         kind,
        "natural_key":  arn,
        "display_name": resource_id or arn,
        "attributes":   attrs,
    }
