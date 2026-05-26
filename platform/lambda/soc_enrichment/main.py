"""SOC enrichment Lambda — SQS consumer.

For each drift event:
  1. Load the events row from Aurora
  2. Compute statistical features (features.py — Task 9)
  3. Call LiteLLM with prompt template (llm.py — Task 10)
  4. Parse response + UPDATE events row with ai_* fields

Per spec §4.3: p95 enrichment <30s, hard timeout 90s.
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


from features import compute_features  # noqa: F401  (re-exported for tests to monkeypatch via main.compute_features)
from llm import call_llm  # noqa: F401


def handler(event: dict, context: Any) -> dict:
    for rec in event.get("Records", []):
        body = json.loads(rec["body"])
        event_id  = body["event_id"]
        tenant_id = body["tenant_id"]

        row = _load_event_row(event_id, tenant_id)
        if row is None:
            print(f"SKIP: event {event_id} not found (vanished)")
            continue

        features: dict = {}
        try:
            features = compute_features(row)
            ai       = call_llm(row, features)
        except Exception as e:
            print(f"WARN: enrichment failed for {event_id}: {e}")
            _update_event_ai(event_id=event_id,
                             narrative=None, anomaly_class=None, anomaly_score=None,
                             next_steps=None, features=features,
                             model_version="unavailable", mitre_technique=None)
            continue

        _update_event_ai(
            event_id        = event_id,
            narrative       = ai.get("narrative"),
            anomaly_class   = ai.get("anomaly_class"),
            anomaly_score   = ai.get("anomaly_score"),
            next_steps      = ai.get("next_steps"),
            features        = features,
            model_version   = ai.get("model_version", os.environ.get("SOC_ENRICHMENT_LLM_MODEL", "claude-sonnet-4-6")),
            mitre_technique = ai.get("mitre_technique"),
        )

    return {"ok": True}


def _load_event_row(event_id: str, tenant_id: str) -> dict | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT e.event_id::text, e.tenant_id::text, e.source, e.kind, e.severity, "
            "       e.title, e.actor, e.resource_arn, e.fired_at::text, "
            "       d.before_state::text, d.after_state::text "
            "FROM events e LEFT JOIN drift_events d USING (event_id) "
            "WHERE e.event_id = CAST(:e AS UUID) AND e.tenant_id = CAST(:t AS UUID)"
        ),
        parameters=[
            {"name": "e", "value": {"stringValue": event_id}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    r = rows[0]
    cols = ["event_id","tenant_id","source","kind","severity","title","actor","resource_arn","fired_at","before_state","after_state"]
    out: dict = {}
    for col, cell in zip(cols, r):
        if cell.get("isNull"):
            out[col] = None
        elif col in ("before_state", "after_state") and "stringValue" in cell:
            out[col] = json.loads(cell["stringValue"]) if cell["stringValue"] else None
        elif "stringValue" in cell:
            out[col] = cell["stringValue"]
        else:
            out[col] = next(iter(cell.values()))
    return out


def _update_event_ai(*, event_id: str, narrative: str | None, anomaly_class: str | None,
                     anomaly_score: int | None, next_steps: list | None,
                     features: dict, model_version: str, mitre_technique: str | None) -> None:
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE events SET "
            "  ai_narrative      = :narrative, "
            "  ai_anomaly_class  = :anomaly_class, "
            "  ai_anomaly_score  = :anomaly_score, "
            "  ai_next_steps     = CAST(:next_steps AS JSONB), "
            "  ai_features       = CAST(:features  AS JSONB), "
            "  ai_model_version  = :model_version, "
            "  ai_enriched_at    = now(), "
            "  mitre_technique   = :mitre "
            "WHERE event_id = CAST(:e AS UUID)"
        ),
        parameters=[
            {"name": "e",             "value": {"stringValue": event_id}},
            {"name": "narrative",     "value": ({"stringValue": narrative}      if narrative      else {"isNull": True})},
            {"name": "anomaly_class", "value": ({"stringValue": anomaly_class}  if anomaly_class  else {"isNull": True})},
            {"name": "anomaly_score", "value": ({"longValue":   anomaly_score} if anomaly_score is not None else {"isNull": True})},
            {"name": "next_steps",    "value": ({"stringValue": json.dumps(next_steps)} if next_steps else {"isNull": True})},
            {"name": "features",      "value": {"stringValue": json.dumps(features)}},
            {"name": "model_version", "value": {"stringValue": model_version}},
            {"name": "mitre",         "value": ({"stringValue": mitre_technique} if mitre_technique else {"isNull": True})},
        ],
    )
