"""DDB-backed seen-table for autonomous broadcast idempotency.

Key: sha256(tenant_id || finding_id || scan_id). TTL: 7 days.
"""
from unittest.mock import MagicMock
import pytest


def _setup(monkeypatch):
    from findings_subscriber import idempotency as idem
    fake_table = MagicMock()
    monkeypatch.setattr(idem, "_table", lambda: fake_table)
    return idem, fake_table


def test_seen_returns_false_when_not_in_table(monkeypatch):
    idem, table = _setup(monkeypatch)
    table.get_item.return_value = {}
    assert idem.seen(tenant_id="t", finding_id="f", scan_id="s") is False


def test_seen_returns_true_when_in_table(monkeypatch):
    idem, table = _setup(monkeypatch)
    table.get_item.return_value = {"Item": {"seen_key": "h", "ttl_epoch": 9999}}
    assert idem.seen(tenant_id="t", finding_id="f", scan_id="s") is True


def test_mark_seen_writes_with_ttl(monkeypatch):
    idem, table = _setup(monkeypatch)
    idem.mark_seen(tenant_id="t", finding_id="f", scan_id="s")
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert "seen_key" in item and "ttl_epoch" in item
    # TTL must be ~now + 7 days (in epoch seconds).
    import time
    delta = item["ttl_epoch"] - int(time.time())
    assert 6 * 86400 < delta < 8 * 86400


def test_mark_seen_uses_conditional_write(monkeypatch):
    """Conditional PutItem: only writes if seen_key doesn't exist. Prevents
    a race where two parallel subscribers both call mark_seen — only one
    succeeds; the loser's ConditionalCheckFailedException is swallowed."""
    idem, table = _setup(monkeypatch)
    idem.mark_seen(tenant_id="t", finding_id="f", scan_id="s")
    kwargs = table.put_item.call_args.kwargs
    assert "ConditionExpression" in kwargs
    assert "attribute_not_exists" in kwargs["ConditionExpression"]
