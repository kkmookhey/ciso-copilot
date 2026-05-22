"""Stage 1 + 2 of the Azure scan pipeline.

Stage 1: the selected subscription list is passed in (the connection
already chose it).
Stage 2: a parallel per-subscription footprint probe classifies each
subscription `active` / `empty` / `unknown`.

The probe is injected as a callable so this module stays pure and
unit-testable; the concrete Azure-SDK probe lives in main.py.

Anti-blind-spot invariant: any probe failure — an exception, or a
probe return value that is not `active`/`empty` — classifies the
subscription `unknown`. A subscription is never silently dropped, and a
probe error is never mislabelled `empty`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_VALID = ("active", "empty")


def discover_subscriptions(
    subscription_ids: list[str],
    probe: Callable[[str], str],
    *,
    max_workers: int = 8,
) -> dict[str, str]:
    """Probe each subscription concurrently. `probe(sub_id)` returns
    `active` or `empty`; any exception or other value -> `unknown`.
    Returns {subscription_id: 'active' | 'empty' | 'unknown'}."""
    if not subscription_ids:
        return {}

    def _probe_one(sub_id: str) -> tuple[str, str]:
        try:
            state = probe(sub_id)
            if state not in _VALID:
                print(f"subscription probe {sub_id}: unexpected state "
                      f"{state!r} -> unknown")
                return (sub_id, "unknown")
            return (sub_id, state)
        except Exception as e:
            print(f"subscription probe {sub_id} FAILED: {e} -> unknown")
            return (sub_id, "unknown")

    states: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for sub_id, state in ex.map(_probe_one, subscription_ids):
            states[sub_id] = state
    return states
