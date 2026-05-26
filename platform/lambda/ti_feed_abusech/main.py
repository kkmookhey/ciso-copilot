# platform/lambda/ti_feed_abusech/main.py
"""abuse.ch feed adapter — pulls Feodo + ThreatFox + writes threat_indicators.

Runs hourly via EventBridge cron (set in events-stack.ts).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterable

# _shared modules are vendored into build/ by build.sh
from ti_lookup import Indicator, upsert_indicators, _reload_env  # type: ignore

_FEODO_URL     = os.environ.get("ABUSECH_FEODO_URL",     "https://feodotracker.abuse.ch/downloads/ipblocklist.txt")
_THREATFOX_URL = os.environ.get("ABUSECH_THREATFOX_URL", "https://threatfox.abuse.ch/export/json/recent/")
_HTTP_TIMEOUT = 30


def handler(event, context) -> dict:
    _reload_env()
    now = dt.datetime.now(dt.timezone.utc)

    feodo_text     = _fetch(_FEODO_URL)
    threatfox_json = _fetch(_THREATFOX_URL)

    feodo_inds: list[Indicator] = []
    if feodo_text:
        for ind in parse_feodo(feodo_text):
            ind.first_seen = ind.last_seen = now
            feodo_inds.append(ind)

    threatfox_inds: list[Indicator] = []
    if threatfox_json:
        try:
            data = json.loads(threatfox_json)
        except json.JSONDecodeError:
            data = {}
        for ind in parse_threatfox(data):
            ind.last_seen = now
            # first_seen comes from the feed when available; fall back to now
            threatfox_inds.append(ind)

    total = upsert_indicators(feodo_inds) + upsert_indicators(threatfox_inds)
    print(f"feed=abusech_feodo upserted={len(feodo_inds)} batches={total}")
    print(f"feed=abusech_threatfox upserted={len(threatfox_inds)}")
    return {"ok": True, "feodo": len(feodo_inds), "threatfox": len(threatfox_inds)}


def parse_feodo(text: str) -> Iterable[Indicator]:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # ignore lines that look like headers, e.g. "# Last updated: ..."
        # IP-per-line; a few rows have "IP,PORT" — split on comma defensively
        ip = line.split(",", 1)[0].strip()
        if not ip:
            continue
        # Trust the feed shape — abuse.ch only ships IPv4 in this list.
        yield Indicator(
            value=ip, kind="ip", source="abusech_feodo",
            first_seen=dt.datetime.now(dt.timezone.utc),
            last_seen=dt.datetime.now(dt.timezone.utc),
            confidence=None, tags=["botnet_c2"],
            raw={"feed": "feodo_ipblocklist"},
        )


def parse_threatfox(data: dict[str, list[dict[str, Any]]]) -> Iterable[Indicator]:
    # ThreatFox shape: {epoch_timestamp_string: [list of IOC dicts]}
    for _ts, rows in data.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            ioc_value = (row.get("ioc_value") or "").strip()
            ioc_type  = (row.get("ioc_type")  or "").strip()
            if not ioc_value:
                continue
            kind, value = _normalize_kind(ioc_type, ioc_value)
            if kind is None:
                continue
            confidence = row.get("confidence_level")
            tags       = list(row.get("tags") or [])
            first_seen = _parse_dt(row.get("first_seen")) or dt.datetime.now(dt.timezone.utc)
            yield Indicator(
                value=value, kind=kind, source="abusech_threatfox",
                first_seen=first_seen, last_seen=first_seen,
                confidence=int(confidence) if isinstance(confidence, (int, float)) else None,
                tags=tags,
                raw={
                    "threat_type": row.get("threat_type"),
                    "malware":     row.get("malware"),
                },
            )


def _normalize_kind(ioc_type: str, value: str) -> tuple[str | None, str]:
    """Map ThreatFox `ioc_type` to our threat_indicators.kind taxonomy."""
    t = ioc_type.lower()
    if t == "ip:port":
        # "10.0.0.1:443" → kind=ip, value=10.0.0.1
        return ("ip", value.split(":", 1)[0])
    if t in ("ipv4", "ip"):
        return ("ip", value)
    if t in ("domain", "fqdn"):
        return ("domain", value.lower())
    if t in ("url",):
        return ("url", value)
    if t in ("sha256_hash", "sha256"):
        return ("sha256", value.lower())
    return (None, value)


def _parse_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        # ThreatFox format: "2026-05-24 12:00:00 UTC"
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ciso-copilot-ti/1.0"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                print(f"WARN: {url} returned status {resp.status}")
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"WARN: fetch failed url={url} err={e!r}")
        return None
