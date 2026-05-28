# platform/lambda/ai_scanner/trivy.py
"""Trivy SCA wrapper. Runs trivy fs --format json on a cloned repo path,
parses the output, and converts each vulnerability to an sca_vuln finding row."""
from __future__ import annotations
import json
import subprocess
from typing import Any


_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH":     "high",
    "MEDIUM":   "medium",
    "LOW":      "low",
    "UNKNOWN":  "info",
}


def run_trivy(repo_path: str) -> dict[str, Any]:
    """Run trivy fs against a cloned repo path. Returns parsed JSON output."""
    proc = subprocess.run(
        ["trivy", "fs", "--format", "json", "--severity", "HIGH,CRITICAL",
         "--quiet", "--scanners", "vuln", repo_path],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        print(f"trivy exited {proc.returncode}: {proc.stderr[:500]}")
        return {"Results": []}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(f"trivy output unparseable: {e}; first 500 chars: {proc.stdout[:500]}")
        return {"Results": []}


def parse_trivy_findings(raw: dict[str, Any], *, repo_id: str) -> list[dict]:
    """Convert Trivy JSON to a list of finding rows ready for unified_writer."""
    out = []
    for result in raw.get("Results", []):
        target = result.get("Target", "unknown")
        for v in result.get("Vulnerabilities", []):
            pkg = v.get("PkgName")
            ver = v.get("InstalledVersion")
            cve = v.get("VulnerabilityID")
            if not (pkg and ver and cve):
                continue
            out.append({
                "kind":      "sca_vuln",
                "severity":  _SEVERITY_MAP.get(v.get("Severity", "UNKNOWN"), "info"),
                "title":     f"{pkg} {ver} — {cve}",
                "evidence_packet": {
                    "package":       pkg,
                    "version":       ver,
                    "fixed_version": v.get("FixedVersion"),
                    "cve":           cve,
                    "manifest":      target,
                    "description":   (v.get("Description") or "")[:1000],
                    "repo_id":       repo_id,
                },
            })
    return out
