# Compliance Mapping Engine v2 — Architecture Spec

> Generalizes the framework registry shipped in AI Visibility Slice 3
> (see `2026-05-24-ai-visibility-v2-slice-3-design.md`) into a
> source-agnostic, family-aware compliance crosswalk engine. Every
> finding the platform stores carries canonical, auditor-defensible
> framework tags across every applicable family, regardless of which
> scanner produced it.
>
> Author: 2026-05-24, KK + Claude. Supersedes the original §15 "out of
> scope" extensions (A2 broad crosswalk, customer-specific overrides)
> by addressing them as part of the engine, not a future slice.

## 1. Goal

A single registry that:

1. **Normalizes** scanner-emitted framework tags to the published
   canonical format for each framework (`GOVERN-6` → `GOVERN 6.1`,
   `EUAI-15` → `Article 15`, `LLM01` → `LLM01:2025`, etc.).
2. **Augments** findings with additional framework tags they should
   carry but don't (e.g., `sagemaker-notebook-root-access` carries only
   `soc2` + `fedramp` from Shasta; registry adds NIST AI RMF + ISO
   42001 + EU AI Act + NIST AI 600-1 + relevant OWASP entries).
3. **Stays source-agnostic** — the same rule fires whether the finding
   originated from AWS, Azure, GCP, Entra, code, or a future source
   (Google Workspace, M365, Slack, GitHub Enterprise Server, etc.)
   provided the finding's properties (check_id, domain, evidence) match.
4. **Stays framework-agnostic** — adding a new framework family
   (privacy, sector-specific, regional) is a JSON edit with zero
   engine code change.

The architecture's stress test: when a 5th scanner source emits a
new finding kind tomorrow, the engine should require **zero code
changes** to give that finding every applicable framework tag from
every applicable family.

## 2. Empirical context (2026-05-24)

The Slice 3 engine shipped and works. Real-data verification on KK's
tenant after deploying the CFN AI fixtures (`CisoBriefFeatureDevAi
Resources`) surfaced two architectural gaps the Slice 3 design didn't
foresee:

### 2.1 Shasta tags in non-canonical format

Shasta emits framework tags using its own internal shorthand, which
diverges from the published canonical IDs in every framework except
MITRE ATLAS:

| Framework | Shasta emits | Published canonical |
|---|---|---|
| `nist_ai_rmf` | `GOVERN-6`, `MANAGE-3`, `MEASURE-2` (function-level) | `GOVERN 6.1`, `MANAGE 3.2`, `MEASURE 2.7` (subcategory, space) |
| `nist_ai_600_1` | `GAI-1` through `GAI-10` (Shasta shorthand) | NIST AI 600-1 Suggested Actions e.g. `MS-3.4-001`, or RMF-style `MEASURE 3.4` |
| `eu_ai_act` | `EUAI-10`, `EUAI-12`, `EUAI-15` | `Article 10`, `Article 12`, `Article 15` |
| `owasp_llm_top10` | `LLM01`, `LLM05` (no year) | `LLM01:2025`, `LLM05:2025` (year-pinned) |
| `mitre_atlas` | `AML.T0012`, `AML.T0051` | (same — Shasta got this right) |
| `iso_42001` | `AI-A.8`, `AI-A.9`, `AI-8.4` (prefix `AI-`) | `A.7.2`, `A.8.3` (no prefix) |
| `owasp_agentic` | `AGENTIC04`, `AGENTIC05`, `AGENTIC10` | (taxonomy not yet stable) |

The platform stores both formats today, so `/ai`'s framework tile
counts effectively double-count and a per-control drill-down would
show two parallel control catalogs under each framework.

### 2.2 Framework gaps by check_id

Some Shasta checks tag comprehensively (8+ frameworks); others are
sparse. Sample from KK's post-fixture scan:

| check_id | Shasta tags |
|---|---|
| `lambda-ai-api-keys-not-hardcoded` | 9 frameworks: soc2, fedramp, pci_dss, iso_42001, mitre_atlas, nist_ai_rmf, nist_ai_600_1, owasp_agentic, owasp_llm_top10 ✓ |
| `bedrock-guardrails-configured` | 9 frameworks (same families) ✓ |
| `sagemaker-notebook-root-access` | **only soc2 + fedramp** — missing AI frameworks entirely |
| `bedrock-vpc-endpoint` | 5 frameworks — missing iso_42001, nist_ai_rmf, owasp_agentic |

Gaps are not random; they reflect when Shasta's mapping was authored.
Filling them is rule-authorship work; making the framework menu
extensible is engine work.

## 3. Decisions

| # | Decision | Reason |
|---|---|---|
| D-1 | **Two-stage write pipeline**: `scanner emit → normalize → augment → store`. Normalize step rewrites Shasta-format IDs to canonical. Augment step is the existing additive-merge from Slice 3. | Separates "what the scanner emits" from "what the platform stores". Future scanner format changes don't break the platform's tag catalog. |
| D-2 | **Canonical format is the published source format** for each framework. Locked per-framework in the framework declaration. | Auditor-defensibility. An auditor cross-referencing a finding's `nist_ai_rmf: ["GOVERN 6.1"]` tag against NIST AI 100-1 finds that exact subcategory in the doc. |
| D-3 | **Rewrite is source-cited** — every synonym → canonical mapping carries a `source` field referencing the publication that establishes the mapping. | Same audit-defensibility commitment as Slice 3 §14.1. |
| D-4 | **Frameworks declare a `family`** (`security`, `ai`, `privacy`, `industry`, future). Web routes filter by family. | Enables `/ai`, `/compliance`, future `/privacy` views without per-route schema changes. |
| D-5 | **Rules are source-agnostic**. Selectors match on finding properties (check_id, domain, evidence_packet, resource_type, ai_touching), never on originating scanner. | Future 5th source slots in without rule rewrites — same gap → same tags. |
| D-6 | **Rule fan-out is intended**. A single rule can `add_frameworks` across all families (security + AI + privacy + industry). One finding picks up tags from every family the underlying gap touches. | Matches KK's "AI is a lens, not a silo" principle generalized — every framework is a lens on the same finding. |
| D-7 | **No backfill scaffolding**. The engine only operates on the write path. Data purges between now and production handle catch-up; production launch will start with a clean schema. | Build-phase pragmatism. Saves dormant code rot. |
| D-8 | **Framework `family` controls categorization, not registry behavior**. The engine treats all families identically — only UI and reporting differentiate. | Keeps the engine generic. Adding a privacy family in the future doesn't introduce engine branching. |
| D-9 | **Auditor-format IDs are the storage format**. Scanner output gets normalized; we never store Shasta's shorthand. | Single source of truth. Per-control rollups and per-finding drill-downs always see the canonical ID. |
| D-10 | **No engine consumes the framework's `family` field**. It's metadata for UI/reporting. The engine never branches on `family`. | Prevents the engine from becoming a forest of family-specific code paths. |

## 4. Architecture

```
┌─ Scanner (any source: AWS, Azure, GCP, Entra, code, future) ─────────┐
│  Emits Finding with native framework tags                            │
│    e.g., {"nist_ai_rmf": ["GOVERN-6"], "soc2": ["CC6.1"]}            │
└──────────────────────┬───────────────────────────────────────────────┘
                       │
                       ▼
              ┌─────────────────────────────────┐
              │   STAGE 1 — NORMALIZE           │
              │   For each (framework, ctrls):  │
              │     Apply framework's           │
              │     rewrite_rules to canonical  │
              │     {"nist_ai_rmf":             │
              │       ["GOVERN 6.1", "GOVERN    │
              │        6.2", "GOVERN 6.3"]}     │
              └──────────────┬──────────────────┘
                             │
                             ▼
              ┌─────────────────────────────────┐
              │   STAGE 2 — AUGMENT             │
              │   For each rule in registry:    │
              │     If selectors match,         │
              │     set-union add_frameworks    │
              │     into finding's frameworks   │
              │     (per Slice 3 semantic)      │
              │   Write provenance              │
              │     _registry_rule_ids          │
              └──────────────┬──────────────────┘
                             │
                             ▼
              ┌─────────────────────────────────┐
              │   STAGE 3 — STORE               │
              │   INSERT INTO findings          │
              └─────────────────────────────────┘
```

**Key properties:**

- **Backward compatible**: a framework with no `rewrite_rules` block
  is normalize-passthrough. Today's Slice 3 registry behavior is the
  degenerate case.
- **Idempotent end-to-end**: re-running both stages on an already
  normalized + augmented finding produces the same output.
- **Order matters**: normalize runs BEFORE augment. Augment's
  `add_frameworks` clauses are also written in canonical format (rule
  authorship is canonical-only), so the merge produces canonical
  output without re-normalization.
- **One hook site per scanner**: same as Slice 3. Each scanner's
  commit path calls `apply(finding, entity_index)` which internally
  runs both stages.

## 5. Framework declaration schema

```json
{
  "frameworks": {
    "nist_ai_rmf": {
      "name":             "NIST AI RMF",
      "family":           "ai",
      "source":           "NIST AI 100-1 (2023)",
      "source_url":       "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
      "version":          "1.0",
      "canonical_format": "FUNCTION SUBCATEGORY (space separator, e.g., GOVERN 6.1)",

      "rewrite_rules": [
        {
          "from": "GOVERN-6",
          "to":   ["GOVERN 6.1", "GOVERN 6.2", "GOVERN 6.3", "GOVERN 6.4", "GOVERN 6.5"],
          "source": "NIST.AI.100-1.pdf table at p.28 — GOVERN 6 category contains 6.1-6.5 subcategories"
        },
        {
          "from": "MEASURE-2",
          "to":   ["MEASURE 2.1", "MEASURE 2.2", "MEASURE 2.3", "MEASURE 2.4", "MEASURE 2.5", "MEASURE 2.6", "MEASURE 2.7", "MEASURE 2.8", "MEASURE 2.9", "MEASURE 2.10", "MEASURE 2.11", "MEASURE 2.12", "MEASURE 2.13"],
          "source": "NIST.AI.100-1.pdf table at p.32-33"
        }
      ],

      "control_descriptions": {
        "GOVERN 6.1": "Policies and procedures are in place that address AI risks associated with third-party entities...",
        "MEASURE 2.7": "AI system security and resilience – as identified in the MAP function – are evaluated and documented."
      }
    },

    "soc2": {
      "name":             "SOC 2 Trust Services Criteria",
      "family":           "security",
      "source":           "AICPA TSC 2017 (with 2022 updates)",
      "source_url":       "https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc-2",
      "version":          "2022",
      "canonical_format": "CC-prefixed control with subcategory (e.g., CC6.1)",
      "rewrite_rules":    [],
      "control_descriptions": {
        "CC6.1": "Logical and physical access controls protect against unauthorized access to information assets."
      }
    },

    "gdpr": {
      "name":             "EU General Data Protection Regulation",
      "family":           "privacy",
      "source":           "Regulation (EU) 2016/679",
      "source_url":       "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
      "version":          "2016/679",
      "canonical_format": "Article N or Article N(P)",
      "rewrite_rules":    [],
      "control_descriptions": {}
    }
  },

  "rules": [
    {
      "id": "sagemaker_notebook_root_access_controls",
      "when": {
        "check_id_eq": "sagemaker-notebook-root-access"
      },
      "add_frameworks": {
        "nist_ai_rmf":     ["MEASURE 2.7", "MANAGE 3.2"],
        "iso_42001":       ["A.7.2", "A.8.3"],
        "eu_ai_act":       ["Article 15"],
        "nist_ai_600_1":   ["MEASURE 2.7"],
        "owasp_llm_top10": ["LLM06:2025"]
      }
    }
  ]
}
```

**Required per-framework fields:**
- `name`, `family`, `source`, `source_url`, `version`, `canonical_format`
- `rewrite_rules` (may be empty array)
- `control_descriptions` (may be empty object)

**`rewrite_rules` semantics:**
- `from`: a single scanner-emitted ID (string)
- `to`: array of canonical IDs (one or more — function-level shorthand
  may legitimately expand to multiple subcategories)
- `source`: human-readable citation for the mapping
- Multiple rewrite rules can match the same `from` only if their `to`
  arrays are merged (set-union); no rule overwrites another's mapping
- Unknown `from` IDs are pass-through (the scanner-emitted ID stays as-is,
  with a `registry_normalize_passthrough{framework, from}` counter
  incremented for observability)

## 6. Rule schema

**Unchanged from Slice 3 §5** except:
- All control IDs in `add_frameworks` MUST be in canonical format
  (validated at registry load — rule referencing a non-canonical ID is
  a hard error at scanner image cold-start)
- The 6 selector kinds (`check_id_eq`, `check_id_glob`, `domain`,
  `resource_type_glob`, `ai_touching`, `evidence_packet_eq`) stay
  identical
- Idempotency, additive merge, provenance via `_registry_rule_ids`
  all preserved

## 7. Data flow

### 7.1 Write-time (per Section 4 architecture)

`apply(finding, entity_index, registry=None)` runs both stages
internally. Each scanner's commit path calls `apply` once per finding,
same as Slice 3 — no scanner-side change beyond the existing hook.

```
for f in findings:
    finding_view = build_finding_view(f, entity_index)
    apply(finding_view)         # Internally: normalize then augment
    write_back(finding_view, f) # Mutate the FindingEmission / param dict
    log_counters(finding_view)
```

### 7.2 Read-time

Read paths unchanged. `/ai/summary`, `/compliance/summary`,
`/findings` all read `findings.frameworks` JSONB. Because everything
in the column is now canonical, no read-time normalization needed.

The `/ai` view's `_AI_FRAMEWORKS` tuple expands from 8 framework keys
to whatever family `ai` contains — derived from the registry's
`frameworks.<key>.family == "ai"` at startup.

### 7.3 Authorship-time

**Adding a new framework** (e.g., GDPR):
1. Add an entry to `frameworks` block with name, family, source, etc.
2. (Optional) Add `rewrite_rules` if any scanner emits non-canonical
   GDPR IDs (initially: nobody, so empty array)
3. (Optional) Add `control_descriptions` as rules cite them
4. Add rules with `add_frameworks: {"gdpr": ["Article 32"]}` matching
   the appropriate check_ids
5. Push, deploy. Re-scans pick up the new tags.

**Adding a new scanner source** (e.g., Google Workspace):
1. New scanner module emits findings via the same `unified_writer`
   pattern; calls `apply_registry()` in its commit path
2. Add rules matching the new source's check_id patterns
3. **No engine code change**

## 8. Source-agnostic guarantee — "5th source" sanity check

The spec is binding only if the following test passes by inspection:

> A new scanner emits a finding:
> ```python
> Finding(
>   check_id="gws_oauth_consent_overpermissive",
>   domain="iam",
>   resource_type="GoogleWorkspace::OAuthApp",
>   evidence_packet={"app_scope": "drive.readonly,gmail.readonly,calendar.readonly", "is_ai": "true"},
>   frameworks={}
> )
> ```
>
> The platform must be able to tag this finding with controls from
> NIST AI RMF + GDPR + SOC 2 + ISO 27001 by adding rules to the
> registry JSON. **No engine code change. No web code change. No
> schema migration.**

If the design fails this test, it's wrong.

The verification:
- New scanner wires `apply_registry()` into its commit path
  (one-line, copy from Slice B's pattern)
- Rule added to registry:
  ```json
  {
    "id": "gws_oauth_consent_overpermissive_controls",
    "when": { "check_id_eq": "gws_oauth_consent_overpermissive" },
    "add_frameworks": {
      "nist_ai_rmf":  ["GOVERN 6.1", "GOVERN 3.2"],
      "iso_42001":    ["A.5.3"],
      "soc2":         ["CC6.1", "CC6.2"],
      "iso27001":     ["A.5.18"],
      "gdpr":         ["Article 32"]
    }
  }
  ```
- Finding lands tagged with 5 frameworks × multiple controls each
- `/ai`'s AI-family tile counts include this finding because the AI
  family contains `nist_ai_rmf` and `iso_42001`
- `/compliance`'s SOC 2 + ISO 27001 + GDPR tiles also include it
- `/findings?framework=gdpr&groupby=category` shows it under
  the IAM domain category

## 9. Error handling

Three layers, same shape as Slice 3 §7, extended for normalize stage:

1. **Module-load validation** — every `rewrite_rules.to` entry must
   be a valid canonical ID (validated against the framework's
   declared `canonical_format` if a regex is given, else against
   `control_descriptions` keys if non-empty). Every `add_frameworks`
   control ID in a rule must also be canonical. A registry that
   references a non-canonical ID fails scanner image cold-start.

2. **Commit-time exception wrapping** — `apply` runs in try/except
   inside the writer; failure logs `registry_apply_failed{stage, rule_id}`
   and continues with the finding's pre-apply tags. Scan never fails
   because of the registry.

3. **Read-time tolerance** — unchanged.

**Observability counters (per scan):**
- `registry_normalize_rewrote{framework, from→to}` — count of times
  each rewrite fired
- `registry_normalize_passthrough{framework, from}` — count of
  scanner-emitted IDs with no rewrite rule (data-quality drift indicator)
- `registry_apply_failed{stage, rule_id}` — per-finding exceptions
- `registry_rule_fire_count{rule_id}` — Slice 3 preserved

## 10. Testing

**Three layers**, matching Slice 3 §8:

### 10.1 Unit tests — `scanner_core/tests/test_framework_registry.py`
Existing 23 tests preserved + new:
- Per-framework: `normalize()` rewrites scanner-format to canonical
- Per-framework: `normalize()` passes through unknown IDs with counter
- Idempotency: `apply(apply(f)) == apply(f)` end-to-end
- Family declaration: registry validates every framework has `family`
- Source-agnostic test: synthetic finding with `check_id` matching
  a rule fires regardless of any "source" attribute
- The Section 8 "5th source" test as an actual integration test (load
  registry, add synthetic Google Workspace rule, assert end-to-end)

### 10.2 Integration tests — per scanner
Each scanner's existing test suite asserts a known finding lands with
canonical-format tags after the writer commits. Existing tests update
to expect canonical IDs.

### 10.3 Read-side regression
`ai_summary/tests/test_main.py` — assert all family-`ai` frameworks
roll up correctly with canonical IDs.

## 11. Success criteria

- Every finding stored in `findings.frameworks` uses canonical IDs
  (no Shasta-format strings present post-engine-deploy + scan)
- `/ai` framework tiles count canonical-format controls (e.g.,
  `GOVERN 6.1` and `MEASURE 2.7` count separately, not collapsed
  with Shasta's function-level shorthand)
- `sagemaker-notebook-root-access` (and any other check_ids gap-filled
  in S3) carries AI framework tags after rescan
- Section 8 5th-source test passes by adding only a rule to the JSON
- All scanner test suites green
- Scan-completion latency unchanged within ±5%
- Per-rule fire counters non-empty in CloudWatch
- Auditor-spot-check: pick 5 random findings, click into their
  framework tags, every tag resolves to the source document

## 12. Authoritative sources for canonical formats

Per-framework binding declaration. Implementer source-verifies each
`from→to` rewrite against the document below before the PR merges.
PR template includes a checklist requiring one citation per rewrite
rule.

| Family | Framework key | Canonical format | Source document | URL |
|---|---|---|---|---|
| ai | `nist_ai_rmf` | `FUNCTION SUBCATEGORY` (space, e.g., `GOVERN 6.1`) | NIST AI 100-1 (Jan 2023) | https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf |
| ai | `nist_ai_600_1` | NIST AI 600-1 Suggested Actions (e.g., `MS-3.4-001`) | NIST AI 600-1 (Jul 2024) | https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf |
| ai | `iso_42001` | ISO clause (e.g., `5.3`, `7.4`) or Annex A (e.g., `A.7.2`), no prefix | ISO/IEC 42001:2023 | iso.org (paywalled) |
| ai | `eu_ai_act` | `Article N` or `Article N(P)` | Regulation (EU) 2024/1689 | https://eur-lex.europa.eu/eli/reg/2024/1689/oj |
| ai | `owasp_llm_top10` | `LLMNN:2025` (year-pinned) | OWASP LLM Top 10 (2025) | https://genai.owasp.org/llm-top-10/ |
| ai | `mitre_atlas` | `AML.TNNNN` | MITRE ATLAS Matrix (v4) | https://atlas.mitre.org/matrices/ATLAS |
| ai | `owasp_agentic` | TBD — taxonomy stabilizing | OWASP Agentic AI Top 10 | https://genai.owasp.org/ |
| ai | `soc2_ai` | per AICPA Description Criteria when obtained | AICPA Description Criteria for AI Systems | aicpa.org (paywalled) |
| security | `soc2` | `CC[1-9].[1-9]` (e.g., `CC6.1`) | AICPA TSC 2017 (with 2022 updates) | aicpa.org |
| security | `iso27001` | `A.N.N.N` clause notation (e.g., `A.5.18`) | ISO/IEC 27001:2022 | iso.org (paywalled) |
| security | `cis_aws` | `N.N.N` (e.g., `2.1.1`) | CIS AWS Foundations Benchmark | cisecurity.org |
| security | `cis_azure` | same | CIS Azure Foundations Benchmark | cisecurity.org |
| security | `cis_gcp` | same | CIS GCP Foundations Benchmark | cisecurity.org |
| security | `nist_800_53` | `XX-N` family-control (e.g., `AC-2`, `SC-7`) | NIST SP 800-53 Rev 5 | nvlpubs.nist.gov |
| security | `fsbp` | AWS Foundational Security Best Practices (control ID per AWS doc) | AWS Security Hub | docs.aws.amazon.com |
| security | `mcsb` | Microsoft Cloud Security Benchmark (e.g., `NS-1`, `IM-1`) | Microsoft Defender for Cloud docs | learn.microsoft.com |
| security | `fedramp` | NIST 800-53 control ID (e.g., `AC-2`, `IA-5`) | FedRAMP Rev 5 baseline | fedramp.gov |
| industry | `pci_dss` | `N.N.N` (e.g., `3.5.1`, `8.6.2`) | PCI DSS v4.0 | pcisecuritystandards.org |
| industry | `hipaa` | `164.NNN(X)(Y)` (e.g., `164.312(a)(1)`) | HHS HIPAA Security Rule | hhs.gov |
| privacy | `gdpr` (future) | `Article N` or `Article N(P)` | EU Regulation 2016/679 | https://eur-lex.europa.eu/eli/reg/2016/679/oj |

## 13. Implementation notes

- **Engine ships before authorship** (same pattern as Slice 3). First
  PR lands the schema + normalize() + empty rewrite_rules everywhere
  → zero behavior change. Subsequent PRs populate the rewrite tables
  one framework at a time, each source-cited.
- **Backward compatible during the transition**: until rewrite_rules
  are authored for a framework, that framework's tags pass through
  unchanged. `/ai` may temporarily show both formats; no breakage.
- **Per-framework PRs** for the rewrite tables. Easier review, isolated
  blast radius if a mapping is wrong.
- **Per-rule fire counters** (Slice 3 §7) become more useful: drop in
  fire count after a normalize-rule lands tells us scanner output is
  now reaching the canonical IDs.

## 14. Dependencies

- **AI Visibility Slice 3 shipped** (commits `ae6e8ac` through
  `26357af`). CME-v2 extends it; no rollback.
- **Slice E rules** (commit `7c7bf18`) must be rewritten to canonical
  format as part of CME-v2 Slice 3.
- No external dependencies (no SDK updates, no third-party services).

## 15. Migration from Slice 3 engine

Slice 3's `apply()` becomes `_augment_stage()`. New public surface:

```python
def apply(finding, entity_index, registry=None):
    """Two-stage compliance crosswalk."""
    _normalize_stage(finding, registry=registry)
    _augment_stage(finding, entity_index, registry=registry)
    return finding
```

Existing tests that call `apply` continue to work unchanged. New
tests target `_normalize_stage` directly. The framework JSON gains
the new `family`, `source_url`, `version`, `canonical_format`,
`rewrite_rules` fields — all optional during transition, required
once CME-v2 Slice 2 lands populated tables for each framework.

## 16. Out of scope (explicit)

- **Per-tenant rule overrides** — customers may want to map a specific
  check_id to a custom internal framework. Future. Add a
  `tenant_overrides` block in the registry JSON when needed.
- **Cross-framework synonym resolution** — `nist_ai_rmf MEASURE 2.7`
  and `nist_ai_600_1 MS-2.7-001` may semantically overlap. The engine
  doesn't merge these; they remain distinct controls under distinct
  frameworks. UI is responsible for any "see also" cross-references
  if needed.
- **Control mapping confidence scoring** — not all crosswalks are
  equally strong (some are direct, some are loose). Future iteration
  could add `confidence: "high" | "medium" | "low"` per rule. Out of
  scope for v2.
- **i18n of control descriptions** — English only for now. Source
  documents are mostly published in English; localized control names
  are out of scope.

## 17. Open questions

1. **NIST AI RMF subcategory rewrite — what's the right `to` mapping
   for Shasta's `GOVERN-6`?** Function `GOVERN 6` in NIST AI 100-1
   contains subcategories `6.1` through `6.5` (estimated). The mapping
   `GOVERN-6 → [6.1, 6.2, 6.3, 6.4, 6.5]` is correct if all five
   apply equally; if Shasta's check is testing only one specific
   subcategory, expanding to all five over-counts. Resolution: read
   Shasta's check code (or its check title/description) to determine
   which subcategory the check actually covers, and rewrite to that
   subset. Implementer pass for CME-v2 Slice 2.
2. **EU AI Act article paragraph specificity** — `Article 9` (article-
   level) vs `Article 9(2)(d)` (paragraph-level)? Article-level is
   easier to author + cite; paragraph-level is more precise. Decision:
   article-level by default, with paragraph-level for the cases where
   Shasta's check or our rule clearly addresses a specific paragraph.
3. **OWASP Agentic taxonomy** — still draft. Hold the framework's
   rewrite_rules empty until the published taxonomy stabilizes; do
   not author rules against `AGENTIC04` etc. in the meantime.
