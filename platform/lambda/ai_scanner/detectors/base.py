# platform/lambda/ai_scanner/detectors/base.py
"""Detector emission types — domain-agnostic.

After SP1, detectors emit (kind, natural_key) pairs that the writer
dedupes on (tenant_id, kind, natural_key). The repo asset is no longer
implicit in source_repo_id — it's an entity referenced by natural_key
the same way every other entity is."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class EntityEmission:
    tenant_id:        str
    kind:             str           # 'ai_framework' | 'ai_model' | 'github_repo' | ...
    natural_key:      str           # per-kind canonical key — see spec §5
    display_name:     str
    domain:           str           # 'ai' | 'cloud' | 'repo' | 'identity' | 'asm'
    attributes:       dict[str, Any]
    evidence_packet:  dict[str, Any] | None
    detector_id:      str
    detector_version: str
    connection_id:    str | None = None
    source_path:      str | None = None   # optional, for UI / evidence


@dataclass(frozen=True)
class EdgeEmission:
    tenant_id:            str
    source_kind:          str        # entity kind of source
    source_natural_key:   str
    target_kind:          str
    target_natural_key:   str
    kind:                 str         # 'uses' | 'calls' | 'deploys_to' | ...
    attributes:           dict[str, Any]
    evidence_packet:      dict[str, Any]
    detector_id:          str
    detector_version:     str


@dataclass(frozen=True)
class FindingEmission:
    tenant_id:                  str
    finding_type:               str
    severity:                   str
    title:                      str
    description:                str
    subject_entity_kind:        str | None
    subject_entity_natural_key: str | None
    subject_type:               str | None
    subject_ref:                str | None
    evidence_packet:            dict[str, Any]
    confidence:                 str
    frameworks:                 dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectorResult:
    entities:      list[EntityEmission] = field(default_factory=list)
    edges:         list[EdgeEmission]    = field(default_factory=list)
    findings:      list[FindingEmission] = field(default_factory=list)


class Detector(Protocol):
    detector_id:      str
    detector_version: str

    def detect(self, ctx: "Any") -> DetectorResult: ...
