# platform/lambda/ti_feed_tor/main.py
"""Tor exit list hourly ETL. Single endpoint, text dump of all current exits."""
from __future__ import annotations

import datetime as dt
import os
import urllib.error
import urllib.request
from typing import Iterable

from ti_lookup import Indicator, upsert_indicators, _reload_env  # type: ignore

_TOR_URL = os.environ.get("TOR_BULK_EXIT_URL", "https://check.torproject.org/torbulkexitlist")


def handler(event, context) -> dict:
    _reload_env()
    body = _fetch(_TOR_URL)
    if body is None:
        return {"ok": False, "error": "fetch_failed"}
    indicators = list(parse_tor(body))
    upsert_indicators(indicators)
    print(f"feed=tor upserted={len(indicators)}")
    return {"ok": True, "count": len(indicators)}


def parse_tor(text: str) -> Iterable[Indicator]:
    now = dt.datetime.now(dt.timezone.utc)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield Indicator(
            value=line, kind="ip", source="tor",
            first_seen=now, last_seen=now,
            confidence=None, tags=["tor_exit"],
            raw={},
        )


def _fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ciso-copilot-ti/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"WARN: tor returned status {resp.status}")
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"WARN: tor fetch failed: {e!r}")
        return None
