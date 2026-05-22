# app/tests/test_scan_pipeline.py
"""scan_pipeline runs scan units concurrently, merges their emissions,
isolates failures, and bounds per-service concurrency."""
import threading
import time

from scan_pipeline import ConcurrencyLimiter, ScanUnit, run_units


def _unit(name, service, entities=(), findings=(), raises=None, sleep=0.0):
    def _run():
        if sleep:
            time.sleep(sleep)
        if raises:
            raise raises
        return {"entities": list(entities), "edges": [], "findings": list(findings)}
    return ScanUnit(name=name, service=service, run=_run)


def test_run_units_merges_emissions():
    units = [
        _unit("a", "ec2", entities=["e1"], findings=["f1"]),
        _unit("b", "s3", entities=["e2"], findings=["f2", "f3"]),
    ]
    res = run_units(units, limiter=ConcurrencyLimiter())
    assert sorted(res.entities) == ["e1", "e2"]
    assert sorted(res.findings) == ["f1", "f2", "f3"]
    assert {o.name: o.status for o in res.outcomes} == {"a": "success", "b": "success"}


def test_run_units_isolates_a_failing_unit():
    units = [
        _unit("ok", "ec2", findings=["f1"]),
        _unit("bad", "ec2", raises=RuntimeError("boom")),
    ]
    res = run_units(units, limiter=ConcurrencyLimiter())
    assert res.findings == ["f1"]
    outcomes = {o.name: o.status for o in res.outcomes}
    assert outcomes == {"ok": "success", "bad": "error"}


def test_run_units_marks_stragglers_timeout():
    units = [
        _unit("fast", "ec2", findings=["f1"]),
        _unit("slow", "ec2", findings=["f2"], sleep=2.0),
    ]
    res = run_units(units, limiter=ConcurrencyLimiter(), batch_timeout=0.5)
    assert res.findings == ["f1"]
    outcomes = {o.name: o.status for o in res.outcomes}
    assert outcomes["fast"] == "success"
    assert outcomes["slow"] == "timeout"


def test_concurrency_limiter_caps_per_service():
    limiter = ConcurrencyLimiter(default=2)
    live = 0
    peak = 0
    lock = threading.Lock()

    def _run():
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.1)
        with lock:
            live -= 1
        return {"entities": [], "edges": [], "findings": []}

    units = [ScanUnit(name=f"u{i}", service="ec2", run=_run) for i in range(6)]
    run_units(units, limiter=limiter, max_workers=6)
    assert peak <= 2  # the ec2 cap held despite 6 workers
