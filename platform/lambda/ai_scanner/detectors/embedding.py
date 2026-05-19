"""Detect embedding generation: OpenAI text-embedding-*, Voyage, Cohere.

Emits one ``embedding`` asset per (file, provider, model) tuple and a
``repository → generates → embedding`` relationship per asset. No findings.
"""
from __future__ import annotations

import re

from detectors.base import AssetEmission, RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.embedding"
detector_version = "0.1.0"

OPENAI_MODEL_RE = re.compile(
    r'\bmodel\s*=\s*["\'](text-embedding-[a-z0-9.\-]+)["\']'
)
EMBED_CALL_RE = re.compile(r'\.embed\s*\(')


def detect(ctx) -> DetectorResult:
    assets: list[AssetEmission] = []
    rels:   list[RelEmission] = []
    seen: set[tuple[str, str, str]] = set()
    repo_ref = f"repository::::{ctx.repo_asset_id}"

    for py in sorted(ctx.repo_workdir.rglob("*.py")):
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        rel_path = str(py.relative_to(ctx.repo_workdir))

        if "openai" in text:
            for m in OPENAI_MODEL_RE.finditer(text):
                model_id = m.group(1)
                _emit(ctx, "openai", model_id, text, m.start(), rel_path,
                       repo_ref, assets, rels, seen)

        if "voyageai" in text or "voyage" in text:
            for m in EMBED_CALL_RE.finditer(text):
                if "voyage" not in text[max(0, m.start() - 100):m.start()].lower():
                    continue
                _emit(ctx, "voyage", "embed", text, m.start(), rel_path,
                       repo_ref, assets, rels, seen)
                break  # one signal per file is enough for voyage

        if "cohere" in text:
            for m in EMBED_CALL_RE.finditer(text):
                if "cohere" not in text[max(0, m.start() - 100):m.start()].lower():
                    continue
                _emit(ctx, "cohere", "embed", text, m.start(), rel_path,
                       repo_ref, assets, rels, seen)
                break

    return DetectorResult(assets=assets, relationships=rels, findings=[])


def _emit(ctx, provider: str, model_id: str, text: str, offset: int,
          rel_path: str, repo_ref: str, assets: list, rels: list,
          seen: set) -> None:
    key = (rel_path, provider, model_id)
    if key in seen:
        return
    seen.add(key)

    line_no = text[:offset].count("\n") + 1
    lines = text.splitlines()
    snippet = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""
    name = f"{provider}/{model_id}"

    packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_asset", subject_type="embedding", subject_name=name,
        source_events=[{
            "kind": "file", "repo": ctx.repo_full_name,
            "commit_sha": ctx.head_commit_sha,
            "path": rel_path, "snippet_lines": [line_no, line_no],
            "snippet": snippet,
        }],
        reasoning_chain=[
            f"matched {provider} embedding signal at {rel_path}:{line_no}"
        ],
        confidence="high",
    )
    assets.append(AssetEmission(
        tenant_id=ctx.tenant_id, connection_id=ctx.connection_id,
        asset_type="embedding", name=name,
        source_repo_id=ctx.repo_asset_id, source_path=rel_path,
        attributes={"provider": provider, "model_id": model_id},
        evidence_packet=packet,
        detector_id=detector_id, detector_version=detector_version,
    ))

    rel_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type="generates",
        subject_name=f"repo→generates→{name}",
        source_events=[], reasoning_chain=["embedding use detected in repo"],
        confidence="high",
    )
    rels.append(RelEmission(
        tenant_id=ctx.tenant_id,
        source_asset_ref=repo_ref,
        target_asset_ref=f"embedding::{ctx.repo_asset_id}::{rel_path}::{name}",
        relationship_type="generates",
        attributes={"provider": provider},
        evidence_packet=rel_packet,
        detector_id=detector_id, detector_version=detector_version,
    ))
