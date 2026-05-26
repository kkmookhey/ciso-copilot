# Threat intel in CISO Copilot SOC

Every drift event we surface in `/soc` is enriched with threat-intel
matches drawn from public feeds we maintain on your behalf. When a
match fires, the `/soc` detail pane shows a labeled badge per source
and tag, and the AI narrative names the source(s) directly.

## Feeds we pull

| Source | Frequency | What it catches |
|---|---|---|
| abuse.ch Feodo Tracker | Hourly | Active botnet C2 IPs (Emotet, Heodo, Dridex, TrickBot) |
| abuse.ch ThreatFox | Hourly | Malware C2 indicators across IPs, domains, hashes |
| CISA Known Exploited Vulnerabilities | Daily | CVEs with confirmed exploitation in the wild |
| Tor Project exit list | Hourly | Tor exit node IPs |
| GreyNoise Community | On-demand, rate-limited | Per-IP classification when our cached feeds miss |

## Customer cost

**None.** All feeds are free and run on our infrastructure. GreyNoise
Community is capped at 30 lookups/tenant/day so a single noisy tenant
cannot exhaust our quota.

## Opt-out

Threat-intel enrichment is server-side. There is nothing to disable on
your cloud accounts. If you want to suppress AI narrative entirely on
a drift event, the existing per-tenant LLM spend cap (default $10/day)
will short-circuit the call once exceeded.

## Where you see it

`/soc` → click any drift event → "Threat intel" section in the detail
pane shows one badge per match (source + tags + confidence when the
source supplies one). The AI narrative cross-references the matches
in plain language ("Source IP is a Tor exit + abuse.ch Feodo C2").

## What we extract from each event

| Event source | What we look up |
|---|---|
| CloudTrail (`AWS API Call via CloudTrail`) | The calling IP (`sourceIPAddress`), plus any IPs / domains / sha256 hashes that appear in the request payload |
| AWS Config (`Configuration Item Change Notification`) | IPs / domains / hashes that appear in the before-state or after-state |

We never look up RFC1918, loopback, link-local, CGNAT (100.64.0.0/10),
or other reserved-IP ranges — those are tenant-internal by definition
and not worth a TI query.
