"""subscription_discovery probes each selected subscription in parallel
and classifies it active / empty / unknown."""
from subscription_discovery import discover_subscriptions


def test_classifies_from_probe_results():
    def probe(sub_id):
        return {"s-active": "active", "s-empty": "empty"}[sub_id]
    states = discover_subscriptions(["s-active", "s-empty"], probe)
    assert states == {"s-active": "active", "s-empty": "empty"}


def test_probe_exception_yields_unknown():
    def probe(sub_id):
        if sub_id == "s-bad":
            raise RuntimeError("boom")
        return "active"
    states = discover_subscriptions(["s-ok", "s-bad"], probe)
    assert states == {"s-ok": "active", "s-bad": "unknown"}


def test_unexpected_probe_value_yields_unknown():
    states = discover_subscriptions(["s-1"], lambda s: "garbage")
    assert states == {"s-1": "unknown"}


def test_empty_input():
    assert discover_subscriptions([], lambda s: "active") == {}
