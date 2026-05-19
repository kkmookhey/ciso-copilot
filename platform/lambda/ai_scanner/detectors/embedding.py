"""Detect embedding generation: OpenAI text-embedding-*, Voyage, Cohere.

Emits one ``ai_embedding`` entity per (provider, model_id) tuple deduped
across files/repos, and a ``github_repo → generates → ai_embedding`` edge
per (repo, embedding) pair. No findings.

SP1 natural key: ``f"{provider}/{model_id}"`` — e.g.
``"openai/text-embedding-3-small"``, ``"voyage/embed"``, ``"cohere/embed"``.
"""
from __future__ import annotations

import re

from detectors.base import EntityEmission, EdgeEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.embedding"
detector_version = "0.2.0"

OPENAI_MODEL_RE = re.compile(
    r'\bmodel\s*=\s*["\'](text-embedding-[a-z0-9.\-]+)["\']'
)
EMBED_CALL_RE = re.compile(r'\.embed\s*\(')


def detect(ctx) -> DetectorResult:
    # Dedup by natural_key across files: same (provider, model_id) seen in
    # two files still emits ONE entity (first-seen file wins for evidence
    # and source_path).
    entities_by_nk: dict[str, EntityEmission] = {}
    edges_by_nk:    dict[str, EdgeEmission]   = {}
    repo_nk = f"github.com/{ctx.repo_full_name}"

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
                       repo_nk, entities_by_nk, edges_by_nk)

        if "voyageai" in text or "voyage" in text:
            for m in EMBED_CALL_RE.finditer(text):
                if "voyage" not in text[max(0, m.start() - 100):m.start()].lower():
                    continue
                _emit(ctx, "voyage", "embed", text, m.start(), rel_path,
                       repo_nk, entities_by_nk, edges_by_nk)
                break  # one signal per file is enough for voyage

        if "cohere" in text:
            for m in EMBED_CALL_RE.finditer(text):
                if "cohere" not in text[max(0, m.start() - 100):m.start()].lower():
                    continue
                _emit(ctx, "cohere", "embed", text, m.start(), rel_path,
                       repo_nk, entities_by_nk, edges_by_nk)
                break

    return DetectorResult(
        entities=list(entities_by_nk.values()),
        edges=list(edges_by_nk.values()),
        findings=[],
    )


def _emit(ctx, provider: str, model_id: str, text: str, offset: int,
          rel_path: str, repo_nk: str,
          entities_by_nk: dict, edges_by_nk: dict) -> None:
    nk = f"{provider}/{model_id}"
    if nk in entities_by_nk:
        return

    line_no = text[:offset].count("\n") + 1
    lines = text.splitlines()
    snippet = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""

    packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_asset", subject_type="embedding", subject_name=nk,
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
    entities_by_nk[nk] = EntityEmission(
        tenant_id=ctx.tenant_id, kind="ai_embedding",
        natural_key=nk, display_name=nk, domain="ai",
        attributes={"provider": provider, "model_id": model_id},
        evidence_packet=packet,
        detector_id=detector_id, detector_version=detector_version,
        connection_id=ctx.connection_id, source_path=rel_path,
    )

    rel_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type="generates",
        subject_name=f"repo→generates→{nk}",
        source_events=[], reasoning_chain=["embedding use detected in repo"],
        confidence="high",
    )
    edges_by_nk[nk] = EdgeEmission(
        tenant_id=ctx.tenant_id,
        source_kind="github_repo", source_natural_key=repo_nk,
        target_kind="ai_embedding", target_natural_key=nk,
        kind="generates", attributes={"provider": provider},
        evidence_packet=rel_packet,
        detector_id=detector_id, detector_version=detector_version,
    )
