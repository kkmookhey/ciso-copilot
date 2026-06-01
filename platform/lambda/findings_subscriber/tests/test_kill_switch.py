"""SSM-backed global kill switch with 60s in-memory cache."""
from unittest.mock import MagicMock


def _setup(monkeypatch, ssm_response=None, ssm_raises=None):
    from findings_subscriber import kill_switch as ks
    ks._cache = (0.0, True)  # reset
    fake = MagicMock()
    if ssm_raises:
        fake.get_parameter.side_effect = ssm_raises
    else:
        fake.get_parameter.return_value = ssm_response or {
            "Parameter": {"Value": "true"}}
    monkeypatch.setattr(ks, "_ssm", fake)
    return ks, fake


def test_enabled_returns_true_when_ssm_says_true(monkeypatch):
    ks, _ = _setup(monkeypatch, {"Parameter": {"Value": "true"}})
    assert ks.global_enabled() is True


def test_enabled_returns_false_when_ssm_says_false(monkeypatch):
    ks, _ = _setup(monkeypatch, {"Parameter": {"Value": "false"}})
    assert ks.global_enabled() is False


def test_enabled_fail_open_when_ssm_throws(monkeypatch):
    """Flaky SSM shouldn't silence the alerts. Per-tenant toggle in
    Aurora is the authoritative kill — global SSM is paranoid layer only."""
    ks, _ = _setup(monkeypatch, ssm_raises=RuntimeError("ssm down"))
    assert ks.global_enabled() is True


def test_cache_hit_doesnt_recall_ssm(monkeypatch):
    """Within 60s, repeated calls hit the in-memory cache."""
    ks, fake = _setup(monkeypatch, {"Parameter": {"Value": "true"}})
    ks.global_enabled()
    ks.global_enabled()
    ks.global_enabled()
    fake.get_parameter.assert_called_once()
