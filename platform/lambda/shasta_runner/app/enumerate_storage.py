"""S3 storage enumeration — emits aws_s3_bucket entities + contains edges.

Region lookup via `get_bucket_location` is best-effort: any failure (Access
Denied, region not in standard set, transient API error) is swallowed and
the bucket is still emitted without a region attribute. We never let a
single bucket's metadata call kill the whole enum.
"""
from __future__ import annotations

from typing import Any

from detectors.base import EdgeEmission, EntityEmission

_DETECTOR_ID      = "shasta_runner.storage"
_DETECTOR_VERSION = "0.1.0"


def enumerate_storage(s3_client, *, account_id: str, tenant_id: str) -> dict[str, list]:
    """Call s3.list_buckets; emit entities + aws_account→contains→bucket edges."""
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []

    resp = s3_client.list_buckets()
    for bucket in resp.get("Buckets", []):
        name = bucket["Name"]
        arn  = f"arn:aws:s3:::{name}"

        attrs: dict[str, Any] = {
            "service": "s3",
            "account": account_id,
        }
        if "CreationDate" in bucket:
            cd = bucket["CreationDate"]
            attrs["creation_date"] = cd.isoformat() if hasattr(cd, "isoformat") else str(cd)

        # Region lookup — best-effort.
        region = _safe_region(s3_client, name)
        if region:
            attrs["region"] = region

        entities.append(EntityEmission(
            tenant_id=tenant_id,
            kind="aws_s3_bucket",
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
            target_kind="aws_s3_bucket",
            target_natural_key=arn,
            kind="contains",
            attributes={},
            evidence_packet={"version": "0.1", "via": "s3.list_buckets"},
            detector_id=_DETECTOR_ID,
            detector_version=_DETECTOR_VERSION,
        ))

    return {"entities": entities, "edges": edges}


def _safe_region(s3_client, bucket_name: str) -> str | None:
    try:
        loc = s3_client.get_bucket_location(Bucket=bucket_name)
        # AWS returns None for us-east-1 (historical wart).
        return loc.get("LocationConstraint") or "us-east-1"
    except Exception:  # noqa: BLE001 — intentional swallow per docstring
        return None
