# scanner_core/scan_pipeline.py
"""Parallel scan-unit pipeline.

All scanner work is expressed as independent ScanUnits, each producing
{entities, edges, findings}. run_units fans them across a thread pool
(the work is I/O-bound — AWS API calls), merges results, isolates
per-unit failures, and bounds per-AWS-service concurrency. The module
is pure orchestration — no AWS, no scanner specifics — so it can be
shared by the Azure / GCP scanners later (spec §11).
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ScanUnit:
    """One independent piece of scan work.

    `run` is a zero-arg callable returning a dict with 'entities',
    'edges', 'findings' lists. `service` keys the per-service
    concurrency cap (e.g. 'ec2', 'iam').
    """
    name:    str
    service: str
    run:     Callable[[], dict]


@dataclass
class UnitOutcome:
    name:   str
    status: str            # 'success' | 'error' | 'timeout'
    detail: str = ""


@dataclass
class UnitResults:
    entities: list = field(default_factory=list)
    edges:    list = field(default_factory=list)
    findings: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)


class ConcurrencyLimiter:
    """Per-AWS-service bounded semaphores. A global max_workers is too
    blunt — services have different throttling limits — so each unit
    acquires its service's slot for the duration of its run."""

    def __init__(self, default: int = 8,
                 per_service: dict[str, int] | None = None):
        self._default = default
        self._per_service = per_service or {}
        self._sems: dict[str, threading.BoundedSemaphore] = {}
        self._lock = threading.Lock()

    def _sem(self, service: str) -> threading.BoundedSemaphore:
        with self._lock:
            sem = self._sems.get(service)
            if sem is None:
                cap = self._per_service.get(service, self._default)
                sem = threading.BoundedSemaphore(cap)
                self._sems[service] = sem
            return sem

    @contextmanager
    def acquire(self, service: str):
        sem = self._sem(service)
        sem.acquire()
        try:
            yield
        finally:
            sem.release()


def run_units(units: list, *,
              limiter: ConcurrencyLimiter,
              max_workers: int = 16,
              batch_timeout: float | None = None) -> UnitResults:
    """Run every unit concurrently; merge results; isolate failures.

    A unit that raises is recorded `error`. A unit still running when
    `batch_timeout` elapses is recorded `timeout` and its eventual
    result is discarded.

    NOTE — `batch_timeout` bounds *result collection*, not wall-clock.
    The ThreadPoolExecutor context manager joins every worker thread on
    exit (`shutdown(wait=True)`), and `future.cancel()` is a no-op once
    a unit is running — so a hung unit still delays this call until its
    thread returns. The real wall-clock bound on a stuck unit is the
    per-AWS-call connect/read timeout in SCAN_BOTO_CONFIG (aws_config.py)
    plus its retry budget; `batch_timeout` only changes how a slow
    unit's outcome is *labelled*. Production callers currently pass no
    `batch_timeout` — the boto timeouts are the bound.

    Each unit holds its service's concurrency slot for its whole run.
    """
    results = UnitResults()
    if not units:
        return results

    def _wrapped(unit: ScanUnit) -> dict:
        with limiter.acquire(unit.service):
            return unit.run()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_unit = {ex.submit(_wrapped, u): u for u in units}
        done, not_done = wait(future_to_unit, timeout=batch_timeout)

        for future in done:
            unit = future_to_unit[future]
            try:
                out = future.result()
                results.entities += out.get("entities", [])
                results.edges    += out.get("edges", [])
                results.findings += out.get("findings", [])
                results.outcomes.append(UnitOutcome(unit.name, "success"))
            except Exception as e:
                print(f"scan unit {unit.name} FAILED: {e}\n"
                      f"{traceback.format_exc()}")
                results.outcomes.append(
                    UnitOutcome(unit.name, "error", str(e)[:200]))

        for future in not_done:
            unit = future_to_unit[future]
            future.cancel()
            print(f"scan unit {unit.name} TIMED OUT (batch deadline)")
            results.outcomes.append(
                UnitOutcome(unit.name, "timeout", "exceeded batch deadline"))

    return results
