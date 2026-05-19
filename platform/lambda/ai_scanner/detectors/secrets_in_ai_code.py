"""Detect hardcoded secrets in files that also import a known LLM SDK.

Correlation gate: a generic secret in a non-AI file is someone else's
problem; this detector only fires when the secret appears in a file that
imports openai / anthropic / bedrock-runtime. One
``hardcoded_credential_in_ai_module`` finding per match. No assets,
no relationships.
"""
from __future__ import annotations

import re
from pathlib import Path

from detectors.base import FindingEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.secrets_in_ai_code"
detector_version = "0.1.0"

SDK_MARKERS = (
    "from openai", "import openai",
    "from anthropic", "import anthropic",
    "bedrock-runtime", "bedrock_runtime",
)

SECRET_PATTERNS = [
    ("openai",    re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{40,}")),
    ("slack",     re.compile(r"xoxb-[A-Za-z0-9\-]{20,}")),
    ("aws",       re.compile(r"AKIA[0-9A-Z]{16}")),
]


def detect(ctx) -> DetectorResult:
    findings: list[FindingEmission] = []

    for py in sorted(ctx.repo_workdir.rglob("*.py")):
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        if not any(m in text for m in SDK_MARKERS):
            continue

        rel_path = str(py.relative_to(ctx.repo_workdir))
        for label, pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                line_no = text[:m.start()].count("\n") + 1
                _emit(ctx, label=label, rel_path=rel_path,
                       line=line_no, findings=findings)

    return DetectorResult(assets=[], relationships=[], findings=findings)


def _emit(ctx, *, label: str, rel_path: str, line: int,
          findings: list) -> None:
    packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="finding", subject_type="hardcoded_credential_in_ai_module",
        subject_name=f"{rel_path}:{line}",
        source_events=[{
            "kind": "file", "repo": ctx.repo_full_name,
            "commit_sha": ctx.head_commit_sha,
            "path": rel_path, "snippet_lines": [line, line],
            "snippet": "(secret redacted)",
        }],
        reasoning_chain=[
            f"matched {label}-shaped secret at {rel_path}:{line} in file with LLM SDK import"
        ],
        confidence="high",
    )
    findings.append(FindingEmission(
        tenant_id=ctx.tenant_id,
        finding_type="hardcoded_credential_in_ai_module",
        severity="high",
        title=f"Hardcoded {label} credential in AI module {rel_path}",
        description=(
            f"A string matching the {label} credential format was found at "
            f"{rel_path}:{line}, in a file that imports a known LLM SDK. "
            "Move the credential out of source and rotate it if real."
        ),
        subject_type="ai_module",
        subject_ref=f"{rel_path}:{line}",
        evidence_packet=packet,
        confidence="high",
    ))
