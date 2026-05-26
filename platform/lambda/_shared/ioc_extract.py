"""Extract IOCs from a drift event row.

Inputs: a row dict with the shape `{source_ip, before_state, after_state}`
(matching what soc_enrichment._load_event_row returns plus the new source_ip
column from migration 013).

Output: `{"ip": [...], "domain": [...], "sha256": [...]}` — deduped, with
RFC1918, loopback, broadcast, and 0.0.0.0/8 IPs filtered out (no point
looking them up against public IOC feeds).
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any

# Anchored to avoid catching version numbers like "10.0" or timestamps.
_IPV4_RE   = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DOMAIN_RE = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})\b", re.IGNORECASE)
_SHA256_RE = re.compile(r"\b[a-f0-9]{64}\b", re.IGNORECASE)

# Networks we never want to look up against public IOC feeds.
# Using explicit CIDRs rather than is_global because Python 3.14 reclassified
# RFC 5737 documentation ranges (e.g. 203.0.113.0/24) as is_private=True,
# but those can appear legitimately in security group rules and are worth checking.
_SKIP_NETS = [
    ipaddress.IPv4Network("10.0.0.0/8"),        # RFC1918
    ipaddress.IPv4Network("172.16.0.0/12"),      # RFC1918
    ipaddress.IPv4Network("192.168.0.0/16"),     # RFC1918
    ipaddress.IPv4Network("127.0.0.0/8"),        # loopback
    ipaddress.IPv4Network("0.0.0.0/8"),          # unspecified / this-network
    ipaddress.IPv4Network("169.254.0.0/16"),     # link-local
    ipaddress.IPv4Network("224.0.0.0/4"),        # multicast
    ipaddress.IPv4Network("240.0.0.0/4"),        # reserved
    ipaddress.IPv4Network("255.255.255.255/32"), # broadcast
]


def _is_lookup_worthy_ipv4(value: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError):
        return False
    return not any(ip in net for net in _SKIP_NETS)


def _walk(node: Any, out: list[str]) -> None:
    """Flatten every string leaf of a possibly-nested JSON-ish structure into `out`."""
    if node is None:
        return
    if isinstance(node, str):
        out.append(node)
        return
    if isinstance(node, dict):
        # CloudTrail wraps lists as {"items": [...]} — walk both the items
        # array and any plain dict value.
        for v in node.values():
            _walk(v, out)
        return
    if isinstance(node, (list, tuple)):
        for v in node:
            _walk(v, out)
        return
    # Numbers, bools — never carry IOCs.


def extract_iocs(row: dict) -> dict[str, list[str]]:
    leaves: list[str] = []
    src_ip = row.get("source_ip")
    if isinstance(src_ip, str):
        leaves.append(src_ip)
    _walk(row.get("before_state"), leaves)
    _walk(row.get("after_state"),  leaves)

    ips:     list[str] = []
    domains: list[str] = []
    hashes:  list[str] = []
    seen_ip:     set[str] = set()
    seen_domain: set[str] = set()
    seen_hash:   set[str] = set()

    for s in leaves:
        for m in _IPV4_RE.findall(s):
            if _is_lookup_worthy_ipv4(m) and m not in seen_ip:
                seen_ip.add(m); ips.append(m)
        for m in _SHA256_RE.findall(s):
            v = m.lower()
            if v not in seen_hash:
                seen_hash.add(v); hashes.append(v)
        # Domain extraction is intentionally last so it doesn't grab IPv4.
        for m in _DOMAIN_RE.findall(s):
            v = m.lower()
            if v in seen_domain:
                continue
            # Reject if the "domain" is just a numeric IPv4
            try:
                ipaddress.IPv4Address(v)
                continue
            except (ipaddress.AddressValueError, ValueError):
                pass
            seen_domain.add(v); domains.append(v)

    return {"ip": ips, "domain": domains, "sha256": hashes}
