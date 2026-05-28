"""SQS-triggered handler for the AI scanner Lambda.

Orchestrates: parse SQS body → clone repo → emit github_repo entity →
run all 8 detectors → correlator → commit transactionally via
unified_writer. On error, marks the scan failed and re-raises so SQS
retries (max 3, then DLQ). RepoTooLarge is terminal.

Spec: docs/superpowers/specs/2026-05-19-sp1-unified-entity-model-design.md §9.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

import boto3

import scan_runner
import trivy as trivy_sca
import unified_writer
from detectors import (
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code,
    crossdomain, correlator,
)
from detectors.base import EntityEmission

logging.basicConfig(level=logging.INFO, force=True)
log = logging.getLogger("ai_scanner")
log.setLevel(logging.INFO)

_sqs = boto3.client("sqs")
_MATCHER_Q = os.environ.get("AI_SUPPLY_CHAIN_MATCHER_QUEUE_URL")

DETECTORS = [
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code, crossdomain,
]


def handler(event: dict, context) -> dict:
    records = event.get("Records") or []
    print(f"[ai_scanner] invoked with {len(records)} record(s)")
    for r in records:
        body = json.loads(r.get("body") or "{}")
        _run_one(body)
    return {"statusCode": 200, "body": json.dumps({"scans_processed": len(records)})}


def _run_one(body: dict) -> None:
    scan_id = body["scan_id"]
    workdir = Path(tempfile.gettempdir()) / f"scan-{scan_id}"
    print(f"[ai_scanner] scan {scan_id} repo={body.get('repo_full_name')} "
          f"branch={body.get('default_branch')} workdir={workdir}")
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)

    ctx = None
    try:
        sha = scan_runner.clone_repo(
            installation_id=body["installation_id"],
            repo_full_name=body["repo_full_name"],
            default_branch=body["default_branch"],
            workdir=workdir,
        )
        ctx = scan_runner.ScanContext.from_message(body, workdir, sha)
        py_count = sum(1 for _ in workdir.rglob("*.py"))
        sql_count = sum(1 for _ in workdir.rglob("*.sql"))
        print(f"[ai_scanner] cloned {ctx.repo_full_name}@{sha} into {workdir} "
              f"({py_count} .py files, {sql_count} .sql files)")

        results = []
        for det in DETECTORS:
            r = det.detect(ctx)
            print(f"[ai_scanner]   {det.detector_id}: "
                  f"{len(r.entities)} entities, {len(r.edges)} edges, "
                  f"{len(r.findings)} findings")
            results.append(r)

        corr_result = correlator.correlate(ctx, results)
        print(f"[ai_scanner]   {correlator.detector_id}: "
              f"{len(corr_result.entities)} entities, {len(corr_result.edges)} edges")

        # Always emit the github_repo entity for this scan — it's the root of
        # every per-repo edge. Detectors reference it by natural_key but don't
        # emit it themselves.
        repo_entity = EntityEmission(
            tenant_id=ctx.tenant_id, kind="github_repo",
            natural_key=f"github.com/{ctx.repo_full_name}",
            display_name=ctx.repo_full_name, domain="repo",
            attributes={"default_branch": ctx.default_branch,
                         "head_commit_sha": ctx.head_commit_sha},
            evidence_packet=None,
            detector_id="manual.repo_attach", detector_version="0.1.0",
            connection_id=ctx.connection_id,
        )

        all_entities = [repo_entity] + [e for r in results for e in r.entities] + corr_result.entities
        all_edges    = [e for r in results for e in r.edges] + corr_result.edges
        all_findings = [f for r in results for f in r.findings] + corr_result.findings

        # === SCA pass via Trivy ===
        # Cap at 60s so we leave headroom for unified_writer.commit_scan under
        # the 600s Lambda ceiling, after clone + 8 detectors + correlator.
        trivy_raw = trivy_sca.run_trivy(str(workdir), timeout=60)
        sca_findings = trivy_sca.parse_trivy_findings(trivy_raw, repo_id=ctx.repo_full_name)
        all_findings.extend(sca_findings)
        print(f"[ai_scanner] trivy: {len(sca_findings)} sca_vuln findings emitted")

        unified_writer.commit_scan(ctx,
                                    entities=all_entities,
                                    edges=all_edges,
                                    findings=all_findings)
        print(f"[ai_scanner] scan {scan_id} committed: "
              f"{len(all_entities)} entities, {len(all_edges)} edges, "
              f"{len(all_findings)} findings")

        # Enqueue the supply-chain matcher so it can join sca_vuln findings
        # with the ai_framework→ai_agent graph and KEV threat indicators.
        if _MATCHER_Q:
            _sqs.send_message(
                QueueUrl=_MATCHER_Q,
                MessageBody=json.dumps({
                    "tenant_id": ctx.tenant_id,
                    "scan_id":   scan_id,
                }),
            )
            print(f"[ai_scanner] matcher enqueued tenant={ctx.tenant_id} scan={scan_id}")

    except scan_runner.RepoTooLarge as e:
        print(f"[ai_scanner] scan {scan_id} aborted: repo too large")
        if ctx is None:
            ctx = scan_runner.ScanContext.from_message(body, workdir, head_commit_sha="")
        unified_writer.mark_scan_failed(ctx, f"clone_too_large: {e}")
    except Exception as e:
        log.exception(f"scan {scan_id} failed")
        if ctx is not None:
            unified_writer.mark_scan_failed(ctx, f"{type(e).__name__}: {e}")
        raise
    finally:
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
