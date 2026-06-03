"""The reaper's one safety-critical property: a scan with a live ECS task is
NEVER reaped, however old it is. Plus the SCAN_ID extraction that the live-set
relies on."""
import main


def test_handler_spares_live_scans(monkeypatch):
    # B is stale-by-time but has a live task; it must survive.
    monkeypatch.setattr(main, "_stale_scan_ids", lambda: ["A", "B", "C"])
    monkeypatch.setattr(main, "_live_scan_ids", lambda: {"B"})
    reaped = []
    monkeypatch.setattr(main, "_reap", reaped.append)

    result = main.handler({}, None)

    assert reaped == ["A", "C"]          # B spared because a task backs it
    assert result == {"stale": 3, "live": 1, "reaped": 2, "reaped_ids": ["A", "C"]}


def test_handler_reaps_nothing_when_all_live(monkeypatch):
    monkeypatch.setattr(main, "_stale_scan_ids", lambda: ["A", "B"])
    monkeypatch.setattr(main, "_live_scan_ids", lambda: {"A", "B"})
    reaped = []
    monkeypatch.setattr(main, "_reap", reaped.append)

    result = main.handler({}, None)

    assert reaped == []
    assert result["reaped"] == 0


class _FakeEcs:
    def __init__(self, arns_by_status, tasks):
        self._arns = arns_by_status
        self._tasks = tasks

    def get_paginator(self, _name):
        arns = self._arns
        class _P:
            def paginate(self, cluster, desiredStatus):
                return iter([{"taskArns": arns.get(desiredStatus, [])}])
        return _P()

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [self._tasks[a] for a in tasks]}


def test_live_scan_ids_extracts_scan_id_from_overrides(monkeypatch):
    task = {"overrides": {"containerOverrides": [
        {"name": "scanner", "environment": [
            {"name": "TENANT_ID", "value": "t1"},
            {"name": "SCAN_ID", "value": "s-running"},
        ]},
    ]}}
    fake = _FakeEcs({"RUNNING": ["arn1"], "PENDING": []}, {"arn1": task})
    monkeypatch.setattr(main, "ecs", fake)

    assert main._live_scan_ids() == {"s-running"}


def test_live_scan_ids_empty_when_no_tasks(monkeypatch):
    monkeypatch.setattr(main, "ecs", _FakeEcs({"RUNNING": [], "PENDING": []}, {}))
    assert main._live_scan_ids() == set()
