"""Stage 1 + 2 of the GCP scan pipeline.

Stage 1: the project list to scan is passed in (single-project
onboarding gives one; org onboarding will give the user-selected
subset — a later slice).
Stage 2: a parallel per-project footprint probe classifies each project
`active` / `empty` / `unknown`.

The probe is injected as a callable so this module stays pure and
unit-testable; the concrete Shasta-GCP probe lives in main.py.

Anti-blind-spot invariant: any probe failure — an exception, or a probe
return value that is not `active`/`empty` — classifies the project
`unknown`. A project is never silently dropped, and a probe error is
never mislabelled `empty`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_VALID = ("active", "empty")


def discover_projects(
    project_ids: list[str],
    probe: Callable[[str], str],
    *,
    max_workers: int = 8,
) -> dict[str, str]:
    """Probe each project concurrently. `probe(project_id)` returns
    `active` or `empty`; any exception or other value -> `unknown`.
    Returns {project_id: 'active' | 'empty' | 'unknown'}."""
    if not project_ids:
        return {}

    def _probe_one(project_id: str) -> tuple[str, str]:
        try:
            state = probe(project_id)
            if state not in _VALID:
                print(f"project probe {project_id}: unexpected state "
                      f"{state!r} -> unknown")
                return (project_id, "unknown")
            return (project_id, state)
        except Exception as e:
            print(f"project probe {project_id} FAILED: {e} -> unknown")
            return (project_id, "unknown")

    states: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for project_id, state in ex.map(_probe_one, project_ids):
            states[project_id] = state
    return states


def enumerate_projects(client) -> dict[str, str]:
    """Return {project_id: display_name} for every project accessible to
    `client.list_projects()`. Used by the scanner in org mode to refresh
    the connection's `scope.projects` before scanning.

    `client.list_projects()` returns a list of dicts with at least
    `project_id` and (optional) `display_name`. Rows without a
    project_id are skipped; a missing/empty display_name falls back to
    the project_id itself.

    Pure — `client` is duck-typed so the function stays unit-testable
    without the Google SDK."""
    out: dict[str, str] = {}
    for row in client.list_projects():
        pid = (row.get("project_id") or "").strip()
        if not pid:
            continue
        name = (row.get("display_name") or "").strip() or pid
        out[pid] = name
    return out
