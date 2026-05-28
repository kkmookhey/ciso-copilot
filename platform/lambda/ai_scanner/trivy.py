# platform/lambda/ai_scanner/trivy.py
"""Trivy SCA wrapper. Runs trivy fs --format json on a cloned repo path,
parses the output, and converts each vulnerability to an sca_vuln finding row."""
from __future__ import annotations
import json
import subprocess
from typing import Any


# Full severity map kept even though run_trivy filters to HIGH,CRITICAL — keeps
# parse_trivy_findings robust if the --severity filter is widened later, or if
# raw Trivy JSON is parsed by a different caller in the future.
_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH":     "high",
    "MEDIUM":   "medium",
    "LOW":      "low",
    "UNKNOWN":  "info",
}


def run_trivy(repo_path: str, *, timeout: int = 120) -> dict[str, Any]:
    """Run trivy fs against a cloned repo path. Returns parsed JSON output.

    `timeout` caps the subprocess wall time. Default 120s leaves headroom
    under the AI scanner Lambda's 600s ceiling; callers running late in the
    Lambda budget should pass a smaller value.
    """
    proc = subprocess.run(
        ["trivy", "fs", "--format", "json", "--severity", "HIGH,CRITICAL",
         "--quiet", "--scanners", "vuln", repo_path],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        print(f"[ai_scanner] trivy: exited {proc.returncode}: {proc.stderr[:500]}")
        return {"Results": []}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(f"[ai_scanner] trivy: output unparseable: {e}; first 500 chars: {proc.stdout[:500]}")
        return {"Results": []}


def parse_trivy_findings(raw: dict[str, Any], *, tenant_id: str,
                          repo_full_name: str):
    """Convert Trivy JSON to a list of FindingEmission instances ready for
    unified_writer.commit_scan."""
    # Imported lazily to keep this module test-friendly without forcing the
    # FindingEmission dependency on every consumer.
    from detectors.base import FindingEmission

    out = []
    dropped = 0
    repo_natural_key = f"github.com/{repo_full_name}"
    for result in raw.get("Results", []):
        target = result.get("Target", "unknown")
        for v in result.get("Vulnerabilities", []):
            pkg = v.get("PkgName")
            ver = v.get("InstalledVersion")
            cve = v.get("VulnerabilityID")
            if not (pkg and ver and cve):
                dropped += 1
                continue
            description = (v.get("Description") or "")[:1000]
            out.append(FindingEmission(
                tenant_id=tenant_id,
                finding_type="sca_vuln",
                severity=_SEVERITY_MAP.get(v.get("Severity", "UNKNOWN"), "info"),
                title=f"{pkg} {ver} — {cve}",
                description=description or f"{pkg} {ver} affected by {cve}.",
                subject_entity_kind="github_repo",
                subject_entity_natural_key=repo_natural_key,
                subject_type="github_repo",
                subject_ref=repo_full_name,
                evidence_packet={
                    "package":       pkg,
                    "version":       ver,
                    "fixed_version": v.get("FixedVersion"),
                    "cve":           cve,
                    "manifest":      target,
                    "description":   description,
                    "repo_id":       repo_full_name,
                },
                confidence="high",
            ))
    if dropped:
        # Surface schema drift early — a Trivy output change that nukes
        # PkgName/InstalledVersion/VulnerabilityID will manifest here.
        print(f"[ai_scanner] trivy: dropped {dropped} incomplete vuln rows (missing pkg/version/cve)")
    return out
