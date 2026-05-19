"""Detect calls to commercial LLM SDKs (OpenAI, Anthropic, Bedrock).

Strategy: scan .py files for `model="..."` (or `modelId="..."`) strings and
emit one asset per (file, provider, model_id) tuple, but only when the same
file also imports a known SDK. Conservative — only emits when both signals
coincide.
"""
from __future__ import annotations

import re
from pathlib import Path

from detectors.base import AssetEmission, RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.model_usage"
detector_version = "0.1.0"

# (import_marker, provider, model_kwarg)
SDKS = [
    ("from openai",      "openai",    "model"),
    ("import openai",    "openai",    "model"),
    ("from anthropic",   "anthropic", "model"),
    ("import anthropic", "anthropic", "model"),
    ("bedrock-runtime",  "bedrock",   "modelId"),
    ("bedrock_runtime",  "bedrock",   "modelId"),
]


def detect(ctx) -> DetectorResult:
    assets: list[AssetEmission] = []
    rels:   list[RelEmission] = []
    seen: set[tuple[str, str, str]] = set()  # (path, provider, model)

    py_files = sorted(ctx.repo_workdir.rglob("*.py"))
    for f in py_files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        rel_path = str(f.relative_to(ctx.repo_workdir))

        for marker, provider, model_kwarg in SDKS:
            if marker not in text:
                continue
            pat = re.compile(rf'\b{model_kwarg}\s*=\s*["\']([^"\']+)["\']')
            for m in pat.finditer(text):
                model_id = m.group(1)
                key = (rel_path, provider, model_id)
                if key in seen:
                    continue
                seen.add(key)

                line_no = text[:m.start()].count("\n") + 1
                lines = text.splitlines()
                snippet = lines[line_no - 1] if 0 < line_no <= len(lines) else ""

                packet = ev.build(
                    detector_id=detector_id, detector_version=detector_version,
                    subject_kind="ai_asset", subject_type="model",
                    subject_name=f"{provider}/{model_id}",
                    source_events=[{
                        "kind": "file", "repo": ctx.repo_full_name,
                        "commit_sha": ctx.head_commit_sha,
                        "path": rel_path, "snippet_lines": [line_no, line_no],
                        "snippet": snippet,
                    }],
                    reasoning_chain=[
                        f"matched {model_kwarg}=\"{model_id}\" in {provider} SDK call at {rel_path}:{line_no}"
                    ],
                    confidence="high",
                )
                assets.append(AssetEmission(
                    tenant_id=ctx.tenant_id, connection_id=ctx.connection_id,
                    asset_type="model", name=f"{provider}/{model_id}",
                    source_repo_id=ctx.repo_asset_id, source_path=rel_path,
                    attributes={"provider": provider, "model_id": model_id},
                    evidence_packet=packet,
                    detector_id=detector_id, detector_version=detector_version,
                ))

                rel_packet = ev.build(
                    detector_id=detector_id, detector_version=detector_version,
                    subject_kind="ai_relationship", subject_type="calls",
                    subject_name=f"repo→calls→{provider}/{model_id}",
                    source_events=[],
                    reasoning_chain=["model use detected in repo"],
                    confidence="high",
                )
                rels.append(RelEmission(
                    tenant_id=ctx.tenant_id,
                    source_asset_ref=f"repository::::{ctx.repo_asset_id}",
                    target_asset_ref=f"model::{ctx.repo_asset_id}::{rel_path}::{provider}/{model_id}",
                    relationship_type="calls",
                    attributes={"provider": provider},
                    evidence_packet=rel_packet,
                    detector_id=detector_id, detector_version=detector_version,
                ))

    return DetectorResult(assets=assets, relationships=rels, findings=[])
