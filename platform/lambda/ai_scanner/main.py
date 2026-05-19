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

logging.basicConfig(level=logging.INFO, force=True)
log = logging.getLogger("ai_scanner")
log.setLevel(logging.INFO)
# Lambda's Python runtime preconfigures the root logger before user code runs,
# so basicConfig is a no-op without force=True. Use print() too as a belt-and-
# suspenders bypass so we can always see what the scanner did.

DETECTORS = [
    framework, model_usage, mcp_server, agentic_workflow,
    vector_db, embedding, prompt, secrets_in_ai_code,
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
                  f"{len(r.assets)} assets, {len(r.relationships)} rels, {len(r.findings)} findings")
            results.append(r)

        corr_result = correlator.correlate(ctx, results)

        all_assets        = [a for r in results for a in r.assets] + corr_result.assets
        all_relationships = [r for res in results for r in res.relationships] + corr_result.relationships
        all_findings      = [f for r in results for f in r.findings] + corr_result.findings
        writer.commit_scan(ctx, all_assets, all_relationships, all_findings)
        print(f"[ai_scanner] scan {scan_id} committed: "
              f"{len(all_assets)} assets, {len(all_relationships)} rels, {len(all_findings)} findings")

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
