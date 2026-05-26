"""CISA KEV daily ETL. Single endpoint, JSON dump of all current entries."""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from typing import Iterable

from ti_lookup import Indicator, upsert_indicators, _reload_env  # type: ignore

_KEV_URL = os.environ.get(
    "CISA_KEV_URL",
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
)


def handler(event, context) -> dict:
    _reload_env()
    body = _fetch(_KEV_URL)
    if body is None:
        return {"ok": False, "error": "fetch_failed"}
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        print(f"WARN: KEV JSON decode error: {e!r}")
        return {"ok": False, "error": "invalid_json"}

    indicators = list(parse_kev(data))
    upsert_indicators(indicators)
    print(f"feed=kev upserted={len(indicators)}")
    return {"ok": True, "count": len(indicators)}


def parse_kev(data: dict) -> Iterable[Indicator]:
    vulns = data.get("vulnerabilities") or []
    for v in vulns:
        cve = (v.get("cveID") or "").strip()
        if not cve:
            continue
        date_added = _parse_date(v.get("dateAdded")) or dt.datetime.now(dt.timezone.utc)
        is_ransomware = (v.get("knownRansomwareCampaignUse") or "").lower() == "known"
        tags: list[str] = []
        if is_ransomware:
            tags.append("ransomware")
        yield Indicator(
            value=cve, kind="cve", source="kev",
            first_seen=date_added, last_seen=dt.datetime.now(dt.timezone.utc),
            confidence=95 if is_ransomware else 80,
            tags=tags,
            raw={
                "vendor":  v.get("vendorProject"),
                "product": v.get("product"),
                "name":    v.get("vulnerabilityName"),
                "due":     v.get("dueDate"),
            },
        )


def _parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ciso-copilot-ti/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"WARN: KEV returned status {resp.status}")
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"WARN: KEV fetch failed: {e!r}")
        return None
