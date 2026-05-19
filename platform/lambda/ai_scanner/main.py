"""SQS-triggered handler for the AI scanner Lambda.

Orchestrates: parse SQS body → clone repo → run all 8 detectors →
correlator → commit transactionally. On error, marks the scan failed and
re-raises so SQS retries (max 3, then DLQ). RepoTooLarge is terminal —
no retry will help.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path

import scan_runner
import writer
from detectors import (
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code, correlator,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai_scanner")

DETECTORS = [
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code,
]


def handler(event: dict, context) -> dict:
    records = event.get("Records") or []
    log.info("ai_scanner invoked with %d record(s)", len(records))
    for r in records:
        body = json.loads(r.get("body") or "{}")
        _run_one(body)
    return {"statusCode": 200, "body": json.dumps({"scans_processed": len(records)})}


def _run_one(body: dict) -> None:
    scan_id = body["scan_id"]
    workdir = Path(tempfile.gettempdir()) / f"scan-{scan_id}"
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
        log.info("cloned %s@%s into %s", ctx.repo_full_name, sha, workdir)

        results = []
        for det in DETECTORS:
            log.info("running %s", det.detector_id)
            results.append(det.detect(ctx))

        corr_result = correlator.correlate(ctx, results)

        all_assets        = [a for r in results for a in r.assets] + corr_result.assets
        all_relationships = [r for res in results for r in res.relationships] + corr_result.relationships
        all_findings      = [f for r in results for f in r.findings] + corr_result.findings
        writer.commit_scan(ctx, all_assets, all_relationships, all_findings)
        log.info("scan %s committed: %d assets, %d rels, %d findings",
                 scan_id, len(all_assets), len(all_relationships), len(all_findings))

    except scan_runner.RepoTooLarge as e:
        log.warning("scan %s aborted: repo too large", scan_id)
        if ctx is None:
            ctx = scan_runner.ScanContext.from_message(body, workdir, head_commit_sha="")
        writer.mark_scan_failed(ctx, f"clone_too_large: {e}")
    except Exception as e:
        log.exception("scan %s failed", scan_id)
        if ctx is not None:
            writer.mark_scan_failed(ctx, f"{type(e).__name__}: {e}")
        raise
    finally:
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
