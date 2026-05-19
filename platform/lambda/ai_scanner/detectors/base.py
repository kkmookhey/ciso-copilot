# platform/lambda/ai_scanner/detectors/base.py
"""Detector protocol + emission dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class AssetEmission:
    tenant_id:        str
    connection_id:    str
    asset_type:       str
    name:             str
    source_repo_id:   str | None
    source_path:      str | None
    attributes:       dict[str, Any]
    evidence_packet:  dict[str, Any]
    detector_id:      str
    detector_version: str
    # caller assigns id at write time so relationships can reference it
    id:               str | None = None


@dataclass(frozen=True)
class RelEmission:
    tenant_id:           str
    source_asset_ref:    str   # placeholder key used to resolve to source_asset_id at write time
    target_asset_ref:    str
    relationship_type:   str
    attributes:          dict[str, Any]
    evidence_packet:     dict[str, Any]
    detector_id:         str
    detector_version:    str


@dataclass(frozen=True)
class FindingEmission:
    tenant_id:        str
    finding_type:     str          # e.g. "unapproved_provider"
    severity:         str          # "critical" | "high" | "medium" | "low" | "info"
    title:            str
    description:      str
    subject_type:     str          # "ai_asset" | "ai_relationship"
    subject_ref:      str
    evidence_packet:  dict[str, Any]
    confidence:       str          # "high" | "medium" | "low"


@dataclass(frozen=True)
class DetectorResult:
    assets:        list[AssetEmission] = field(default_factory=list)
    relationships: list[RelEmission]    = field(default_factory=list)
    findings:      list[FindingEmission] = field(default_factory=list)


class Detector(Protocol):
    detector_id:      str
    detector_version: str

    def detect(self, ctx: "Any") -> DetectorResult: ...
