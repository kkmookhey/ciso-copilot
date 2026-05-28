# platform/lambda/_shared/speakable.py
"""Friendly spoken labels for entity rows.

Tool results and push payloads carry paired {speakable, identifier} fields
so Shasta never reads an ARN/UUID/sha256 aloud. The model speaks the
`speakable` field; it passes `arn`/`upn`/`object_id` only when piping to
another tool.
"""
from __future__ import annotations
from typing import Any


_LABEL_BY_KIND = {
    "aws_lambda":       lambda n: f"the {n} Lambda",
    "aws_s3_bucket":    lambda n: f"the {n} bucket",
    "aws_ec2_instance": lambda n: f"the {n} EC2 instance",
    "aws_iam_role":     lambda n: f"the {n} IAM role",
    "aws_iam_user":     lambda n: f"the {n} IAM user",
    "ai_agent":         lambda n: f"the {n} agent",
    "ai_framework":     lambda n: n,                # "langchain" stands alone
    "ai_model":         lambda n: f"the {n} model",
    "ai_tool":          lambda n: f"the {n} tool",
    "ai_mcp_server":    lambda n: f"the {n} MCP server",
    "ai_vector_db":     lambda n: f"the {n} vector database",
    "entra_user":       lambda n: n,                # name or UPN stands alone
    "github_repo":      lambda n: f"your {n} repo",
}


def speakable_entity(entity: dict[str, Any]) -> str:
    """Friendly spoken label for an entity row from the entities table."""
    kind = entity.get("kind", "unknown")
    name = entity.get("display_name") or _short_id(entity.get("natural_key", ""))
    fmt = _LABEL_BY_KIND.get(kind)
    if fmt:
        return fmt(name)
    return f"the {kind} {name}"


def speakable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Walk a dict; for any sub-dict that looks like an entity (has `kind` +
    either `display_name` or `natural_key`), add a `speakable` field. Returns
    a new dict — input is not mutated."""
    if _looks_like_entity(payload):
        out = dict(payload)
        out["speakable"] = speakable_entity(payload)
        return out
    out = {}
    for k, v in payload.items():
        if isinstance(v, dict):
            out[k] = speakable_payload(v)
        elif isinstance(v, list):
            out[k] = [speakable_payload(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out


def _looks_like_entity(d: dict[str, Any]) -> bool:
    return "kind" in d and ("display_name" in d or "natural_key" in d)


def _short_id(natural_key: str) -> str:
    """For when no display_name exists — keep last segment, first 8 chars.
    Splits on both '/' (paths) and ':' (ARNs), taking the rightmost non-empty segment.
    """
    if not natural_key:
        return "unknown"
    import re
    parts = re.split(r"[/:]", natural_key)
    tail = next((p for p in reversed(parts) if p), natural_key)
    return tail[:8]
