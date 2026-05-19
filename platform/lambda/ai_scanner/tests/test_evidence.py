"""Tests for the EvidencePacket builder."""
from __future__ import annotations

import re

import pytest


def test_build_packet_shape():
    import evidence
    p = evidence.build(
        detector_id="ai.detectors.framework", detector_version="0.1.0",
        subject_kind="ai_asset", subject_type="framework", subject_name="langchain",
        source_events=[{"kind": "file", "repo": "kk/foo", "commit_sha": "abc123",
                        "path": "/app/agent.py", "snippet_lines": [12, 12],
                        "snippet": "from langchain import LLMChain"}],
        reasoning_chain=["matched import on line 12"],
        confidence="high",
    )
    assert p["version"] == "0.1"
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                        p["packet_id"])
    assert p["produced_at"].endswith("Z")
    assert p["detector"]["id"] == "ai.detectors.framework"
    assert p["subject"]["kind"] == "ai_asset"
    assert p["subject"]["type"] == "framework"
    assert p["subject"]["name"] == "langchain"
    assert p["source_events"][0]["path"] == "/app/agent.py"
    assert p["model"] is None
    assert p["signature"] is None
    assert p["confidence"] == "high"
