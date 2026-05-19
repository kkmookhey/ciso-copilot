"""Build Trust Evidence Packets per the open Slice-1 spec.

Schema: docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md §7.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

# Use timezone-aware UTC instead of deprecated utcnow()
UTC = dt.timezone.utc


def build(*,
          detector_id: str,
          detector_version: str,
          subject_kind: str,           # "ai_asset" | "ai_relationship" | "finding"
          subject_type: str,           # asset_type / relationship_type / finding_type
          subject_name: str,
          source_events: list[dict[str, Any]],
          reasoning_chain: list[str],
          confidence: str,             # "high" | "medium" | "low"
          subject_id: str | None = None,
          graph_trace: list[Any] | None = None,
          ) -> dict[str, Any]:
    return {
        "version":           "0.1",
        "packet_id":         str(uuid.uuid4()),
        "produced_at":       dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "detector":          {"id": detector_id, "version": detector_version},
        "subject":           {"kind": subject_kind, "id": subject_id,
                              "type": subject_type, "name": subject_name},
        "source_events":     source_events,
        "graph_trace":       graph_trace or [],
        "reasoning_chain":   reasoning_chain,
        "model":             None,     # deterministic detectors — no LLM
        "confidence":        confidence,
        "signature":         None,     # KMS signing deferred
    }
