# AI Visibility v2 — Slice 3 Design

> Compliance mapping sweep: net-new AICPA SOC 2 + AI authorship, plus
> a bidirectional framework crosswalk that lets the existing
> `/findings` view answer "show me ISO 42001 compliance, grouped by
> category" — where AI is one of the categories alongside IAM /
> Storage / Encryption / etc.
>
> Author: brainstormed 2026-05-24 with KK. Belongs to AI Visibility v2
> (see `2026-05-22-ai-visibility-v2-design.md`).

## 1. Goal

Make the existing `/ai` view surface all eight AI frameworks already
present in the data, and make the existing `/findings` view answer
two new user journeys correctly:

- **"I want ISO 42001 compliance"** — show cloud-infra controls under
  IAM / Compute / Storage / Encryption / etc., **plus** an AI row in
  the category breakdown showing AI-specific findings tagged with
  ISO 42001.
- **"I want PCI DSS compliance"** — show cloud controls as today,
  **plus** an AI row that lights up for any AI-touching finding with a
  PCI-relevant gap (e.g., a Bedrock endpoint without TLS).

The slice ships a **single new module** in the unified writer's commit
path. No new web routes, no new Lambdas, no schema migration.

## 2. Empirical context (gathered 2026-05-24)

State of the data before S3 starts. Drives the scope choices below.

**Framework tags already in `findings.frameworks` across both tenants:**

| Framework key | Findings |
|---|---|
| `fedramp` | 7610 |
| `soc2` | 7580 |
| `pci_dss` | 5394 |
| `cis_aws` | 3042 |
| `cis_gcp` | 124 |
| `owasp_agentic` | 102 |
| `mitre_atlas` | 102 |
| `nist_ai_600_1` | 85 |
| `nist_ai_rmf` | 85 |
| `owasp_llm_top10` | 85 |
| `iso_42001` | 68 |
| `eu_ai_act` | 68 |
| `iso27001` | 67 |
| `nist_800_53` | 60 |
| `cis_azure` | 39 |
| `fsbp` | 36 |
| `mcsb` | 24 |
| `hipaa` | 2 |
| `soc2_ai` | **0** |

**`findings.domain` is already a populated category column:**

| Domain | Findings |
|---|---|
| `storage` | 4786 |
| `encryption` | 1061 |
| `logging` | 1001 |
| `networking` | 451 |
| `compute` | 194 |
| `ai` | 120 |
| `monitoring` | 106 |
| `iam` | 87 |

**Read-side plumbing already supports the user journeys:**
- `web/src/routes/TopRisks.tsx` (mounted at `/findings`) supports four
  group dimensions: `status` / `category` / `cloud` / `framework`. The
  `category` dimension groups by `f.domain` (line 108).
- `web/src/routes/Dashboard.tsx` framework tiles already navigate to
  `/findings?framework=<fw>` on click (line 167).
- `compliance_summary` Lambda returns a per-framework rollup (no
  per-category drill — not needed because the drill happens
  client-side in `TopRisks.tsx`).

## 3. Decisions

| # | Decision | Reason |
|---|---|---|
| D-1 | **SOC 2 AI mappings sourced from AICPA's published criteria** for AI Systems (Description Criteria 2024) | KK ask — customer-audit-defensible |
| D-2 | **All eight AI frameworks** get tiles on `/ai`: NIST AI RMF, ISO 42001, SOC 2 AI, EU AI Act, NIST AI 600-1, OWASP LLM Top 10, OWASP Agentic, MITRE ATLAS | Shasta already emits the last four — free signal currently dropped |
| D-3 | **All three S2 Entra sign-in finding kinds** (`personal_tier`, `corp_tier`, `unknown_tier`) get tagged with applicable AI-framework controls | Includes the `corp_tier` (pass) one — keeps the compliance view honest about *all* AI-using employees, not just risky ones |
| D-4 | **Bidirectional crosswalk** — registry adds AI-framework tags to AI-touching cloud findings, AND adds standard-framework tags (PCI / HIPAA / etc.) to AI findings whose underlying gap is relevant. Scope is **AI-touching findings only** (option A1 from the brainstorm); the broader "tag every cloud finding with AI controls" option (A2) is deferred | Stays true to "AI is a lens, not a silo"; prevents `/ai` tile counts from ballooning into the thousands |
| D-5 | **Registry application inline in `unified_writer.commit_scan()`** | One commit path, one function call, additive merge; mirrors S2.1's pattern of inline scan-time enrichment with try/except wrapping |
| D-6 | **Registry stored as JSON in repo**, not a DB table | Diff-friendly for compliance review; matches existing AI SaaS catalog pattern; no schema migration |
| D-7 | **Backfill via re-scan, not SQL migration** | Re-scans are frequent; the cost of a one-off backfill script doesn't earn its complexity unless a customer is mid-audit |
| D-8 | **No `/compliance` view added** — Dashboard framework tile → `/findings?framework=<fw>` → switch group dim to "Category" is the journey | Read-side plumbing is already there; adding a new view duplicates capability |

## 4. Architecture

```
┌─ Existing scanners ─────────────────────────────────────────┐
│  shasta_runner (AWS)                                        │
│  shasta_runner_azure (incl. ai_pass.py — S1)                │
│  shasta_runner_gcp                                          │
│  shasta_runner_entra (incl. ai_signin_pass.py — S2)         │
│  ai_scanner (code)                                          │
└──────────────────────┬──────────────────────────────────────┘
                       │  emits Finding objects
                       ▼
            ┌──────────────────────────────┐
            │ apply_framework_registry(f)  │   ← NEW (S3)
            │ in scanner_core/             │
            │ framework_registry.py        │
            └──────────────┬───────────────┘
                           │  merges control IDs into f.frameworks
                           ▼
            ┌──────────────────────────────┐
            │ unified_writer.commit_scan() │   ← existing
            └──────────────┬───────────────┘
                           ▼
                  ┌─────────────────┐
                  │  findings table │
                  └─────────────────┘
```

**Key properties:**

- One canonical authorship file: `platform/lambda/scanner_core/ai_framework_registry.json`.
- One canonical engine: `scanner_core/framework_registry.py` exposing
  `apply(finding, entity_index)`.
- **Additive only** — existing Shasta-emitted controls are preserved;
  registry only adds missing controls.
- **Idempotent** — `apply(apply(f)) == apply(f)`. Re-scans are safe.
- **Read path unchanged** — `/ai/summary`, `/compliance/summary`,
  `/findings` all read the same `findings.frameworks` JSONB.
- **One read-side delta** — bump `_AI_FRAMEWORKS` in
  `ai_summary/main.py` from 4 keys to 8, and expand the `/ai` tile
  grid in `web/src/routes/AISummary.tsx` from 4 to 8 cells.

## 5. Registry schema

`platform/lambda/scanner_core/ai_framework_registry.json`:

```json
{
  "frameworks": {
    "soc2_ai": {
      "name": "SOC 2 + AI",
      "source": "AICPA Description Criteria for AI Systems (2024)",
      "control_descriptions": {
        "CC6.1-AI": "Logical access to AI systems and training data is restricted.",
        "CC6.7-AI": "AI data is encrypted at rest and in transit.",
        "CC9.1-AI": "Risks from AI use are identified, assessed, and mitigated."
      }
    },
    "nist_ai_rmf":      { "name": "NIST AI RMF",      "source": "NIST AI 100-1 (2023)",     "control_descriptions": { ... } },
    "iso_42001":        { "name": "ISO/IEC 42001",    "source": "ISO/IEC 42001:2023",       "control_descriptions": { ... } },
    "eu_ai_act":        { "name": "EU AI Act",        "source": "Regulation (EU) 2024/1689","control_descriptions": { ... } },
    "nist_ai_600_1":    { "name": "NIST AI 600-1",    "source": "NIST AI 600-1 (2024)",     "control_descriptions": { ... } },
    "owasp_llm_top10":  { "name": "OWASP LLM Top 10", "source": "OWASP 2025",               "control_descriptions": { ... } },
    "owasp_agentic":    { "name": "OWASP Agentic",    "source": "OWASP Agentic AI Top 10",  "control_descriptions": { ... } },
    "mitre_atlas":      { "name": "MITRE ATLAS",      "source": "MITRE ATLAS v4",           "control_descriptions": { ... } }
  },

  "rules": [
    {
      "id": "soc2_ai_encryption_at_rest_for_ai_storage",
      "when": {
        "check_id_glob": "cis_aws_2.1.*",
        "domain":        "storage",
        "ai_touching":   true
      },
      "add_frameworks": {
        "soc2_ai":     ["CC6.7-AI"],
        "nist_ai_rmf": ["MEASURE-2.7"],
        "iso_42001":   ["A.8.2"],
        "eu_ai_act":   ["Article 15"]
      }
    },
    {
      "id": "ai_signin_personal_tier_controls",
      "when": {
        "check_id_eq": "ai_signin_personal_tier"
      },
      "add_frameworks": {
        "soc2_ai":       ["CC6.1-AI", "CC9.1-AI"],
        "nist_ai_rmf":   ["GOVERN-3.2", "MEASURE-2.6"],
        "iso_42001":     ["A.5.3", "A.9.2"],
        "eu_ai_act":     ["Article 4"],
        "owasp_agentic": ["AML.T0028"]
      }
    },
    {
      "id": "ai_resource_pci_relevance",
      "when": {
        "domain":              "ai",
        "resource_type_glob":  "aws_bedrock_*",
        "evidence_packet_eq":  { "handles_cardholder_data": "true" }
      },
      "add_frameworks": {
        "pci_dss": ["3.4", "3.5", "4.1"]
      }
    }
  ]
}
```

**Selectors supported in `when` (AND-ed):**
- `check_id_eq` — exact match on `findings.check_id`.
- `check_id_glob` — glob match (uses Python `fnmatch.fnmatchcase`).
- `domain` — match `findings.domain`.
- `resource_type_glob` — glob match on `findings.resource_type`.
- `ai_touching` — boolean. When true, the rule fires only if the
  finding satisfies the existing `_IS_AI_TOUCHING` predicate (entity
  in `domain='ai'`, AI-relevant entity `kind`, or
  `evidence_packet ->> 'is_ai' = 'true'`).
- `evidence_packet_eq` — dict of `{key: expected_string_value}` matched
  against `findings.evidence_packet`.

**`add_frameworks` semantics:** set-union per framework key. Resulting
control list is deduped and sorted for stable serialisation.

## 6. Data flow

### 6.1 Write-time

```
Scanner emits Finding f
       │
       ▼
unified_writer.commit_scan(scan, findings, entities, edges)
       │
       │  Once per commit (not per finding):
       │  ── Load registry (cached on cold-start)
       │  ── Build entity_index: {entity_id → (domain, kind)}
       │     from the entities being committed
       │  ── For any finding referencing an entity NOT in entity_index,
       │     batched query: SELECT id, domain, kind FROM entities
       │     WHERE id = ANY(:ids). One roundtrip per commit.
       │     The ai_touching predicate also checks the finding's own
       │     evidence_packet ->> 'is_ai' — that's a finding-local
       │     check, no entity lookup needed.
       │
       ▼
for f in findings:
    apply_framework_registry(f, entity_index)
       │  ── Walk rules in declaration order
       │  ── For each rule whose 'when' matches f:
       │       merge rule.add_frameworks into f.frameworks
       │  ── No-op if no rules match
       │
       ▼
INSERT INTO findings (... frameworks ...) VALUES (...)
```

### 6.2 Read-time (unchanged)

All existing read paths continue to read `findings.frameworks` JSONB
exactly as before. The only changes:
- `_AI_FRAMEWORKS` in `ai_summary/main.py` goes from 4 keys to 8.
- `/ai` tile grid in `AISummary.tsx` renders 8 framework tiles.

### 6.3 Authorship-time

To add a new control mapping:
1. Edit `ai_framework_registry.json` — add a rule to `rules`.
2. Add a unit test in `scanner_core/tests/test_framework_registry.py`.
3. Push → merge → deploy scanner images.
4. New findings on next scan carry the new tags; existing findings
   re-tag on next rescan.

**No SQL backfill in S3.** Re-scans naturally backfill within one or
two scan cycles. Worth doing a one-off backfill script only if a
customer is mid-audit and needs immediate accuracy.

## 7. Error handling

Three layers of defense — the registry sits in the hot path of every
scan commit, so failures must not fail scans.

1. **Module-load validation** — `framework_registry.py` validates the
   JSON at import. Every rule has an `id`, a non-empty `when`, a
   non-empty `add_frameworks`; every framework key in `add_frameworks`
   exists in the top-level `frameworks` block; every selector key in
   `when` is a known selector. A malformed registry fails scanner
   image cold-start — found at deploy time, not at 3am.

2. **Commit-time exception wrapping** —
   `apply_framework_registry(finding, entity_index)` is wrapped in
   try/except inside `unified_writer.commit_scan()`. A per-finding
   raise logs `registry_apply_failed: check_id=X rule_id=Y err=...`
   and commits the finding with its original Shasta-emitted
   frameworks. **The scan never fails because of the registry** —
   same shape as S2.1's `ai_signin_pass` try/except.

3. **Read-time tolerance** — `/ai/summary` and `TopRisks.tsx` already
   tolerate findings whose `frameworks` JSONB is missing keys. No
   change needed.

**Soft assertion**: trades "perfect tagging on every finding" for
"scans never fail because of tagging". Failure-mode visibility is via
CloudWatch (`registry_apply_failed` + `registry_ai_touching_unknown`
counters), not scan failures.

## 8. Testing

**Unit tests — `platform/lambda/scanner_core/tests/test_framework_registry.py`** (≥ 12 cases):
- JSON load + schema validation (shipping registry parses).
- One test per selector kind (`check_id_eq`, `check_id_glob`,
  `domain`, `resource_type_glob`, `ai_touching`, `evidence_packet_eq`).
- Additive merge — Shasta-emitted controls preserved, registry
  controls added, deduped + sorted.
- Idempotency — `apply(apply(f)) == apply(f)`.
- `ai_touching` no-op when entity missing from index.
- Error wrapping — broken selector raises a known exception type.

**Integration tests — per-scanner suite**:
- `shasta_runner/tests/` — synthetic AWS commit, assert an S3
  encryption failure on a Bedrock training bucket lands tagged.
- `shasta_runner_azure/tests/` — Azure-AI finding lands tagged.
- `shasta_runner_entra/tests/` — `ai_signin_personal_tier` lands with
  full AI framework stack.
- `ai_scanner/tests/` — code-scanner finding lands tagged.

Integration tests use the **real shipping registry**, not a mock — so
a regression in mappings fails CI.

**Read-side regression — `ai_summary/tests/`**: extends the existing
`test_main.py` to assert all eight frameworks render and the
`_IS_AI_TOUCHING` predicate's behavior is unchanged.

**Out of scope**:
- Per-control authoritative correctness — content review in PR, not CI.
- Live deployment smoke — covered by manual KK-gated browser
  verification (refresh `/ai`, click ISO 42001 tile, switch to
  category grouping, confirm AI row populates).

## 9. Success criteria

- Eight AI framework tiles render on `/ai` with non-zero counts on
  KK's tenant.
- Click ISO 42001 tile from Dashboard → `/findings?framework=iso_42001`
  → switch group to "Category" → **AI row populates** with both
  AI-specific findings and any AI-touching cloud findings.
- Click PCI DSS tile → switch to category → **AI row populates** with
  any Bedrock/SageMaker/Entra finding that has PCI-relevant gaps.
- Existing Shasta-emitted framework tags preserved 1:1 (spot-check 5
  random findings before/after).
- Scan-completion latency unchanged within ±5%.
- All scanner test suites green; new
  `scanner_core/tests/test_framework_registry.py` ≥ 12 cases.

## 10. Open questions

To be resolved during implementation, not blockers:

1. **AICPA SOC 2 + AI control IDs** — the `CC6.1-AI` / `CC6.7-AI` /
   `CC9.1-AI` IDs in this spec are sensible projections of AICPA's
   structure, not quotations. Implementer pulls canonical IDs from
   AICPA's published Description Criteria during authorship. The
   registry file is the only thing that changes if IDs land
   differently.

2. **EU AI Act article references** — initial authorship covers the
   high-value clusters (data governance Art. 10, technical robustness
   Art. 15, transparency Art. 13, human oversight Art. 14, AI
   literacy Art. 4). Broader coverage comes via iterative PRs as
   customers ask.

3. **Which existing cloud check IDs are AI-touching enough to inherit
   AI frameworks?** Start with `ai_touching: true` selectors that lean
   on `findings.domain='ai'` or the entity allowlist — high precision,
   low coverage. Broaden only when a customer asks why a specific
   cloud finding isn't on `/ai`.

## 11. Implementation notes

- Engine and authorship are **independent**: registry can ship with
  empty `rules: []` and break nothing. Land the engine in one PR;
  add mappings in subsequent PRs without coordination.
- **Backfill via re-scan** — no SQL migration. Customers' next Medium+
  scan picks up the new tags.
- **Deploy sequence**: scanner image rebuild (registry + engine) →
  `CisoCopilotApi` hotswap for `_AI_FRAMEWORKS` tuple in `ai_summary`
  → web build for the 8-tile grid. Three independently revertable
  deploys.
- **`build.sh` touch points**: `shasta_runner_azure/build.sh` and
  `shasta_runner_gcp/build.sh` already copy `scanner_core/` into the
  image (Slice 0 pattern). `shasta_runner/build.sh` (AWS) also copies
  it. `shasta_runner_entra/build.sh` does NOT today — small addition
  required.
- **Per-scanner commit-path verification** is the first task of the
  implementation plan. The principle is "apply the registry inside the
  scanner's commit path, additively". For scanners that use
  `unified_writer.commit_scan()` (Azure, GCP, Entra, ai_scanner) the
  hook is a one-line addition. The AWS scanner (`shasta_runner`) may
  use a different path via `scan_pipeline`; the plan-writer should
  verify the AWS commit path and wire the registry accordingly —
  same principle, possibly different call site.

## 12. Dependencies

- **S1 shipped** (Azure AI cloud pass + `/ai` view): registry extends
  the AI-framework keys S1 introduced.
- **S2 shipped** (Entra AI sign-in pass): registry tags the 3 new
  finding kinds.
- **Slice 0 shipped** (shared `scanner_core/`): the registry module
  lives here and rides the existing image-copy mechanism.
- No external dependencies (no SDK updates, no third-party services).

## 13. Out of scope (explicit)

- **A2 / A3 framework crosswalk** — tagging *every* cloud finding with
  AI controls regardless of AI-touching status. Deferred until
  customer feedback shows the A1 lens is too narrow.
- **`/ai` view design changes beyond tile-count expansion** — same
  component, eight cells instead of four. No new charts, no
  drill-down within the `/ai` view itself.
- **iOS-side rendering** — covered by S4.
- **GCP-AI** — separate sub-project; the registry will pick up GCP-AI
  findings naturally when that scanner module lands.
- **Customer-specific overrides** of the registry — future evolution
  (likely a per-tenant overlay JSON in S3.x or beyond).
