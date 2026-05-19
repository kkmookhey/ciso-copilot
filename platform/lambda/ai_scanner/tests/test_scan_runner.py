# platform/lambda/ai_scanner/tests/test_scan_runner.py
"""Tests for the scan runner orchestration layer."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_SECRET_ARN", "arn:fake")
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    monkeypatch.setenv("SCANNER_VERSION", "0.1.0-test")
    # boto3 stubs (cred + secrets access) so module-level imports succeed
    import boto3
    class _FakeSm:
        def get_secret_value(self, SecretId):
            return {"SecretString": json.dumps({
                "app_id": "3763791",
                "client_id": "Iv23liTest",
                "client_secret": "secret",
                "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
            })}
    class _FakeData:
        def execute_statement(self, **kw): return {"records": []}
    monkeypatch.setattr(boto3, "client",
                        lambda name, **_kw: _FakeSm() if name == "secretsmanager" else _FakeData())


def test_scan_context_built_from_sqs_body():
    import scan_runner
    body = {
        "scan_id":         "11111111-1111-1111-1111-111111111111",
        "tenant_id":       "22222222-2222-2222-2222-222222222222",
        "connection_id":   "33333333-3333-3333-3333-333333333333",
        "repo_asset_id":   "44444444-4444-4444-4444-444444444444",
        "repo_full_name":  "kk/foo",
        "default_branch":  "main",
        "installation_id": 99999,
    }
    ctx = scan_runner.ScanContext.from_message(body, repo_workdir=Path("/tmp/x"), head_commit_sha="abc123")
    assert ctx.scan_id == body["scan_id"]
    assert ctx.repo_full_name == "kk/foo"
    assert ctx.installation_id == 99999


def test_clone_repo_uses_installation_token(monkeypatch, tmp_path):
    """clone_repo should call git with the right URL + return commit SHA."""
    import scan_runner

    captured = {}
    def fake_run(cmd, **kw):
        captured.setdefault("cmd", cmd)  # capture first call only (clone); later calls are du/rev-parse
        # write a fake .git/HEAD so the next rev-parse works
        head_dir = Path(kw.get("cwd") or ".") / ".git"
        head_dir.mkdir(parents=True, exist_ok=True)
        (head_dir / "HEAD").write_text("ref: refs/heads/main\n")
        # next call should be rev-parse — return a fixture sha
        return subprocess.CompletedProcess(cmd, 0, stdout=b"deadbeef1234\n", stderr=b"")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(scan_runner, "_installation_token", lambda iid: "ghs_fake_token")

    workdir = tmp_path / "scan"
    sha = scan_runner.clone_repo(installation_id=99999, repo_full_name="kk/foo",
                                  default_branch="main", workdir=workdir)
    assert sha == "deadbeef1234"
    # ensure the clone URL embedded the token
    assert any("x-access-token:ghs_fake_token@github.com/kk/foo.git" in str(part)
               for part in captured["cmd"])


def test_clone_repo_fails_on_oversize(monkeypatch, tmp_path):
    """If the repo exceeds the 4 GB ceiling, raise RepoTooLarge."""
    import scan_runner

    def fake_check_output(cmd, **kw):
        # `du -s -B1 .` returns ~4.5 GB
        return b"4831838208\t.\n"
    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(scan_runner, "_installation_token", lambda iid: "tok")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0] if a else [], 0, stdout=b"abc\n", stderr=b""
    ))

    with pytest.raises(scan_runner.RepoTooLarge):
        scan_runner.clone_repo(installation_id=1, repo_full_name="kk/big",
                                default_branch="main", workdir=tmp_path / "x")


def test_handler_runs_full_scan_pipeline(monkeypatch, tmp_path):
    """End-to-end: SQS record → clone (stubbed) → detectors → unified_writer (stubbed) → success."""
    import main
    import scan_runner
    import unified_writer
    import shutil

    fixture_root = Path(__file__).parent / "fixtures" / "framework" / "langchain_in_repo" / "repo"

    def fake_clone(installation_id, repo_full_name, default_branch, workdir):
        if workdir.exists():
            shutil.rmtree(workdir)
        shutil.copytree(fixture_root, workdir)
        return "deadbeef"
    monkeypatch.setattr(scan_runner, "clone_repo", fake_clone)

    calls = []
    def fake_commit(ctx, *, entities, edges, findings):
        calls.append({"entities":  len(entities),
                      "edges":     len(edges),
                      "findings":  len(findings)})
    monkeypatch.setattr(unified_writer, "commit_scan", fake_commit)
    monkeypatch.setattr(unified_writer, "mark_scan_failed",
                        lambda ctx, msg: calls.append({"failed": msg}))

    sqs_event = {"Records": [{
        "body": json.dumps({
            "scan_id":         "11111111-1111-1111-1111-111111111111",
            "tenant_id":       "22222222-2222-2222-2222-222222222222",
            "connection_id":   "33333333-3333-3333-3333-333333333333",
            "repo_asset_id":   "44444444-4444-4444-4444-444444444444",
            "repo_full_name":  "kk/foo",
            "default_branch":  "main",
            "installation_id": 99999,
        }),
    }]}

    main.handler(sqs_event, None)

    assert len(calls) == 1
    assert "entities" in calls[0]
    # Expect: github_repo entity + langchain ai_framework entity = at least 2
    assert calls[0]["entities"] >= 2


def test_handler_marks_scan_failed_on_repo_too_large(monkeypatch, tmp_path):
    """RepoTooLarge is terminal — mark_scan_failed called, no re-raise."""
    import main
    import scan_runner
    import unified_writer

    def fake_clone(installation_id, repo_full_name, default_branch, workdir):
        raise scan_runner.RepoTooLarge("repo is 5 GB")
    monkeypatch.setattr(scan_runner, "clone_repo", fake_clone)

    calls = []
    monkeypatch.setattr(unified_writer, "commit_scan", lambda *a, **kw: calls.append("commit"))
    monkeypatch.setattr(unified_writer, "mark_scan_failed",
                        lambda ctx, msg: calls.append({"failed": msg}))

    sqs_event = {"Records": [{
        "body": json.dumps({
            "scan_id":         "55555555-5555-5555-5555-555555555555",
            "tenant_id":       "22222222-2222-2222-2222-222222222222",
            "connection_id":   "33333333-3333-3333-3333-333333333333",
            "repo_asset_id":   "44444444-4444-4444-4444-444444444444",
            "repo_full_name":  "kk/bigrepo",
            "default_branch":  "main",
            "installation_id": 99999,
        }),
    }]}

    main.handler(sqs_event, None)

    assert calls == [{"failed": "clone_too_large: repo is 5 GB"}]
