# platform/lambda/ai_scanner/tests/test_trivy.py
import json
from unittest.mock import patch, MagicMock

from trivy import run_trivy, parse_trivy_findings


def test_parse_trivy_findings_extracts_pkg_version_cve():
    raw = {
        "Results": [{
            "Target": "requirements.txt",
            "Vulnerabilities": [{
                "PkgName": "langchain",
                "InstalledVersion": "0.0.184",
                "FixedVersion": "0.0.354",
                "VulnerabilityID": "CVE-2026-0470",
                "Severity": "CRITICAL",
                "Description": "RCE in LLMChain executor",
            }]
        }]
    }
    findings = parse_trivy_findings(raw, repo_id="repo-uuid-abc")
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "sca_vuln"
    assert f["severity"] == "critical"
    assert f["evidence_packet"]["package"] == "langchain"
    assert f["evidence_packet"]["version"] == "0.0.184"
    assert f["evidence_packet"]["fixed_version"] == "0.0.354"
    assert f["evidence_packet"]["cve"] == "CVE-2026-0470"


def test_parse_handles_empty_results():
    assert parse_trivy_findings({"Results": []}, repo_id="x") == []


@patch("trivy.subprocess.run")
def test_run_trivy_invokes_subprocess(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({"Results": []}),
        stderr="",
    )
    result = run_trivy("/tmp/cloned_repo")
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "trivy"
    assert "fs" in cmd
    assert "--format" in cmd
    assert "json" in cmd
    assert "/tmp/cloned_repo" in cmd
    assert result == {"Results": []}
