# FedRAMP + PCI DSS framework mappings — design

> Incremental change #4. Adds FedRAMP (NIST SP 800-53 Rev 5) and
> PCI DSS v4.0.1 control mappings to Shasta cloud findings.
> Created 2026-05-20.

## Problem

Shasta findings carry framework controls via the `Finding` object's
attributes — `soc2_controls`, `cis_aws_controls`, `iso27001_controls`,
`hipaa_controls`, etc. The scanner copies these into
`FindingEmission.frameworks`, persisted to the `findings.frameworks`
JSONB column. FedRAMP and PCI DSS are not Shasta attributes, so findings
carry no FedRAMP/PCI controls and the compliance view cannot score them.

Shasta is a read-only dependency in this project — the mapping must be
added here.

## Decisions

- **PCI DSS v4.0.1** — current standard; v3.2.1 retired March 2024.
- **FedRAMP = NIST SP 800-53 Rev 5 control IDs** (e.g. `SC-28`, `AC-2`).
  FedRAMP has no separate control numbering; it is 800-53 with baselines.
  Framework key: `fedramp`.
- **Scan-time application**, not read-time. `compliance_summary` builds
  per-framework scores by SQL-unnesting the `findings.frameworks` JSONB
  (`jsonb_each`). The controls must be physically in that column or the
  compliance view stays blind to them — so the mapping is written into
  `FindingEmission.frameworks` at scan time. (This differs from the
  check-title catalog, which is read-time because its only consumers
  render display text.)

## Components

### Catalog — single source of truth
`scripts/framework_map.py`:
- `FRAMEWORK_MAP: dict[str, dict[str, list[str]]]` — keyed by `check_id`,
  value is `{"fedramp": [...], "pci_dss": [...]}`. **Partial coverage** —
  a check appears only under a framework it genuinely satisfies; no
  forced mappings (e.g. `aws-tag-policy` maps to neither).
- `merge_framework_map(check_id, frameworks)` — returns a new frameworks
  dict with `fedramp` / `pci_dss` keys added when the catalog has them.

### Distribution
Each scanner Lambda bundles only its own directory. `scripts/sync_framework_map.py`
mirrors the catalog into the four scanner app directories:
`platform/lambda/shasta_runner{,_azure,_gcp,_entra}/app/framework_map.py`.
Committed copies, never hand-edited; re-run after any catalog edit.
Same pattern as `sync_check_titles.py`.

### Application
`merge_framework_map` is called where `FindingEmission.frameworks` is
built:
- `_shasta_to_emission` in each of the four scanner `app/main.py` files.
- `ai_findings_to_emissions` in `shasta_runner/app/ai_pass.py` (the AWS
  AI-check findings — `sagemaker-*`, `bedrock-*`).

The resulting controls land in `findings.frameworks` JSONB.

### Downstream — no change required
`compliance_summary`, `findings_list`, and `findings_rollup` read the
JSONB dynamically. Only the web `FRAMEWORK_LABEL` map gains `fedramp`
and `pci_dss` display names.

## Effect

Existing findings gain FedRAMP/PCI controls on their next scan (scans
run daily). No DB migration.

## Testing

Mirrors the check-title catalog tests:
- Catalog keys are a subset of Shasta's 292 static `check_id`s (no typos).
- Control-ID format sanity: `fedramp` values match `^[A-Z]{2}-\d+`,
  `pci_dss` values match `^\d+\.\d+`.
- No empty control lists.
- `merge_framework_map` unit tests — adds keys for a mapped check, leaves
  frameworks untouched for an unmapped check, does not mutate its input.
- Sync integrity — the four scanner copies are byte-identical to master.

## Out of scope

- FedRAMP baseline tiers (Low / Moderate / High).
- PCI DSS compensating controls and applicability scoping.
- The `ai_scanner` repo detectors (separate concern — those emit
  `frameworks={}` and would map to OWASP LLM / MITRE ATLAS, not FedRAMP).
