# platform/lambda/_shared/ti_lookup.py
"""Shared TI substrate. Two operations against the global threat_indicators table:

* `bulk_lookup({kind: [values]})` — read-side, called by soc_enrichment
* `upsert_indicators(iter[Indicator])` — write-side, called by the cron feed adapters

Vendored into each consuming Lambda via that Lambda's build.sh:
    cp -r ../_shared/*.py build/
The Lambda runtime puts `build/` first on sys.path so flat imports work.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
from typing import Iterable

import boto3

DB_CLUSTER_ARN = ""
DB_SECRET_ARN  = ""
DB_NAME        = ""

rds_data = boto3.client("rds-data")


def _reload_env() -> None:
    """Refresh module-level DB env. Cold start reads from env; tests call this after monkeypatching."""
    global DB_CLUSTER_ARN, DB_SECRET_ARN, DB_NAME
    DB_CLUSTER_ARN = os.environ.get("DB_CLUSTER_ARN", "")
    DB_SECRET_ARN  = os.environ.get("DB_SECRET_ARN", "")
    DB_NAME        = os.environ.get("DB_NAME", "ciso_copilot")


_reload_env()


@dataclasses.dataclass
class Indicator:
    value:      str
    kind:       str          # 'ip' | 'domain' | 'url' | 'sha256' | 'cve'
    source:     str          # 'abusech_feodo' | 'abusech_threatfox' | 'kev' | 'tor' | 'greynoise_community'
    first_seen: dt.datetime
    last_seen:  dt.datetime
    confidence: int | None
    tags:       list[str]
    raw:        dict


def bulk_lookup(values_by_kind: dict[str, list[str]]) -> dict[str, list[dict]]:
    """Return {indicator_value: [{source, kind, confidence, tags}, ...]} for hits.

    `values_by_kind` is shaped `{"ip": [...], "domain": [...], ...}`.
    Empty input → empty dict; never touches DB.
    """
    flat: list[tuple[str, str]] = []
    for kind, values in values_by_kind.items():
        for v in values:
            if v:
                flat.append((kind, v))
    if not flat:
        return {}

    # Build a single SELECT with an `(kind, value) IN (...)` clause.
    # Parameter naming: kN/vN for the Nth pair.
    in_pairs = ", ".join(f"(:k{i}, :v{i})" for i in range(len(flat)))
    sql = (
        "SELECT indicator_value, kind, source, confidence, tags::text "
        "FROM threat_indicators "
        f"WHERE (kind, indicator_value) IN ({in_pairs})"
    )
    parameters: list[dict] = []
    for i, (k, v) in enumerate(flat):
        parameters.append({"name": f"k{i}", "value": {"stringValue": k}})
        parameters.append({"name": f"v{i}", "value": {"stringValue": v}})

    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=sql, parameters=parameters,
    )
    out: dict[str, list[dict]] = {}
    for r in rs.get("records", []):
        value      = r[0].get("stringValue")
        kind       = r[1].get("stringValue")
        source     = r[2].get("stringValue")
        confidence = r[3].get("longValue") if not r[3].get("isNull") else None
        tags_raw   = r[4].get("stringValue") or "[]"
        try:
            tags = json.loads(tags_raw)
        except (TypeError, ValueError):
            tags = []
        out.setdefault(value, []).append(
            {"source": source, "kind": kind, "confidence": confidence, "tags": tags}
        )
    return out


def upsert_indicators(indicators: Iterable[Indicator]) -> int:
    """Upsert indicators in batches. Returns the number of statements executed.

    Uses ON CONFLICT to refresh last_seen + confidence + tags + raw on
    repeat sightings. first_seen is preserved (EXCLUDED only sets newer rows).
    """
    inds = list(indicators)
    if not inds:
        return 0

    sql = (
        "INSERT INTO threat_indicators "
        "  (indicator_value, kind, source, first_seen, last_seen, confidence, tags, raw) "
        "VALUES "
        "  (:value, :kind, :source, CAST(:first_seen AS TIMESTAMPTZ), CAST(:last_seen AS TIMESTAMPTZ), "
        "   :confidence, CAST(:tags AS JSONB), CAST(:raw AS JSONB)) "
        "ON CONFLICT (indicator_value, kind, source) DO UPDATE SET "
        "  last_seen  = EXCLUDED.last_seen, "
        "  confidence = COALESCE(EXCLUDED.confidence, threat_indicators.confidence), "
        "  tags       = EXCLUDED.tags, "
        "  raw        = EXCLUDED.raw"
    )

    # RDS Data API batch_execute_statement supports up to 1000 param sets.
    BATCH = 500
    executed = 0
    for start in range(0, len(inds), BATCH):
        chunk = inds[start:start + BATCH]
        parameter_sets = []
        for ind in chunk:
            parameter_sets.append([
                {"name": "value",      "value": {"stringValue": ind.value}},
                {"name": "kind",       "value": {"stringValue": ind.kind}},
                {"name": "source",     "value": {"stringValue": ind.source}},
                {"name": "first_seen", "value": {"stringValue": ind.first_seen.isoformat()}},
                {"name": "last_seen",  "value": {"stringValue": ind.last_seen.isoformat()}},
                {"name": "confidence", "value": ({"longValue": ind.confidence} if ind.confidence is not None else {"isNull": True})},
                {"name": "tags",       "value": {"stringValue": json.dumps(ind.tags)}},
                {"name": "raw",        "value": {"stringValue": json.dumps(ind.raw, default=str)}},
            ])
        rds_data.batch_execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=sql, parameterSets=parameter_sets,
        )
        executed += 1
    return executed
