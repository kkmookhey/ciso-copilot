"""Fan-out hook for the autonomous CRITICAL-finding Slack broadcast.

Module owns ONE responsibility: best-effort publish to SQS when a
critical-fail finding is written. Failures log and swallow."""
from __future__ import annotations
from unittest.mock import MagicMock


def _install_fake_sqs(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_BROADCAST_QUEUE_URL",
                       "https://sqs.us-east-1.amazonaws.com/000000000000/q")
    from _shared import broadcast_fanout as bf
    fake = MagicMock()
    monkeypatch.setattr(bf, "_sqs", fake)
    return bf, fake


def test_publishes_when_critical_fail(monkeypatch):
    bf, fake = _install_fake_sqs(monkeypatch)
    bf.publish_if_critical(
        tenant_id="t-1", finding_id="f-1", scan_id="s-1",
        severity="critical", status="fail",
    )
    fake.send_message.assert_called_once()
    body = fake.send_message.call_args.kwargs["MessageBody"]
    import json
    payload = json.loads(body)
    assert payload == {
        "tenant_id": "t-1", "finding_id": "f-1", "scan_id": "s-1",
    }


def test_skips_when_not_critical(monkeypatch):
    bf, fake = _install_fake_sqs(monkeypatch)
    bf.publish_if_critical(
        tenant_id="t-1", finding_id="f-1", scan_id="s-1",
        severity="high", status="fail",
    )
    fake.send_message.assert_not_called()


def test_skips_when_not_fail(monkeypatch):
    bf, fake = _install_fake_sqs(monkeypatch)
    bf.publish_if_critical(
        tenant_id="t-1", finding_id="f-1", scan_id="s-1",
        severity="critical", status="pass",
    )
    fake.send_message.assert_not_called()


def test_short_circuits_when_queue_url_unset(monkeypatch):
    """A scanner that hasn't been granted sqs:SendMessage shouldn't crash;
    it should short-circuit silently when the env var is empty."""
    monkeypatch.delenv("AUTONOMOUS_BROADCAST_QUEUE_URL", raising=False)
    from _shared import broadcast_fanout as bf
    fake = MagicMock()
    monkeypatch.setattr(bf, "_sqs", fake)
    bf.publish_if_critical(
        tenant_id="t", finding_id="f", scan_id="s",
        severity="critical", status="fail",
    )
    fake.send_message.assert_not_called()


def test_swallows_sqs_errors(monkeypatch, capsys):
    """A missed broadcast is recoverable; a failed scanner write is not.
    The hook must not propagate SQS errors back to the writer."""
    bf, fake = _install_fake_sqs(monkeypatch)
    fake.send_message.side_effect = RuntimeError("sqs blew up")

    # Must NOT raise.
    bf.publish_if_critical(
        tenant_id="t", finding_id="f", scan_id="s",
        severity="critical", status="fail",
    )
    # But must log loudly so the drift metric catches it.
    out = capsys.readouterr().out
    assert "broadcast_fanout" in out
    assert "sqs blew up" in out
