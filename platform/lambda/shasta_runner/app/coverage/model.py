# app/coverage/model.py
"""Core types for the AWS posture coverage engine.

A collector turns AWS API responses into Resource objects. A Check is a
declarative, deterministic posture rule that evaluates one Resource and
returns an Outcome. The engine runs checks over collected resources and
emits entities/edges/findings. See spec §6.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Resource:
    """One discovered AWS resource, normalized by a collector."""
    service:       str             # boto3 service / client name, e.g. 'sqs'
    resource_type: str             # e.g. 'queue'
    arn:           str             # canonical ARN — the entity natural_key
    name:          str             # human display name
    region:        str
    raw:           dict[str, Any]  # normalized attributes the checks read


@dataclass(frozen=True)
class Outcome:
    """Result of evaluating one Check against one Resource."""
    status:      str               # 'pass' | 'fail' | 'partial'
    evidence:    dict[str, Any]
    remediation: str = ""


@dataclass(frozen=True)
class Check:
    """A declarative, deterministic posture check for one resource type.

    `evaluate` is a pure function: same Resource in, same Outcome out, no
    I/O, no LLM (the determinism invariant, spec §6).
    """
    check_id:      str
    service:       str
    resource_type: str
    title:         str
    severity:      str                     # 'low'|'medium'|'high'|'critical'
    domain:        str                     # finding category, e.g. 'encryption'
    min_tier:      str                     # 'quick'|'medium'|'deep'
    frameworks:    dict[str, list[str]]     # benchmark name -> control ids
    evaluate:      Callable[[Resource], Outcome]
