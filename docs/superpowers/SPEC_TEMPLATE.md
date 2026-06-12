# Spec template — Shasta by Transilience

> Every new spec under `docs/superpowers/specs/` must follow this shape.
> The structure beyond §0 is flexible (use what fits the work). **§0 is
> mandatory and gates commit** — see CLAUDE.md "Verify before claiming
> new" rule.

---

# [Spec title — short, descriptive]

> [Front-matter blockquote: 1-3 sentences on what this spec is, when
> brainstormed, and which adjacent specs/PRs it builds on.]
>
> Cross-refs:
> - [`prior-spec.md`](prior-spec.md) — one-line description of why this matters

## 0. Codebase baseline — verified YYYY-MM-DD

**REQUIRED.** Catalog every file, table, route, Lambda, detector,
migration, and entity-kind that the new work touches or claims to
modify. Fill in by running `grep` / `Read` / `ls` against the actual
codebase, NOT from memory. Cite file paths + line counts + brief
"what's there today" notes.

This section answers one question: **what already exists in the area
this spec touches?** Every later "we'll add X" claim must be checkable
against this section. If §0 already says X exists, the spec describes a
*modification*, not an addition — fix the claim before commit.

### Template

```markdown
## 0. Codebase baseline — verified YYYY-MM-DD

Areas touched by this slice, with file paths + key facts:

- **<Subsystem A>** — `<exact/file/path.py>` (`wc -l` says N lines), 
  patterns mirrored in `<other/path.py>` via `<sync-script.py>` (if
  applicable). **Already shipped:** [bulleted list of capabilities + 
  rules + migrations + entities + routes that exist today].
- **<Table or schema area>** — migration `0NN_<name>.sql` (`platform/sql/`)
  added it on YYYY-MM-DD. Columns: [list]. Foreign keys: [list].
- **<UI surface>** — `<web/src/.../File.tsx>` renders [what]. State 
  managed by [hook/lib]. Reads from [API endpoint].
- **<Detector inventory>** — `<dir>/detectors/` has N files: [list].
- **<What's genuinely new in this slice>** — [one-line summary; 
  everything below in the spec must fall under this scope].
```

### Worked example (from 2026-06-04 AI Security Slice 1)

```markdown
## 0. Codebase baseline — verified 2026-06-04

- **Framework registry** — `scanner_core/framework_registry.py` 
  (281 lines, canonical master), mirrored to `ai_scanner/`, 
  `shasta_runner_{azure,entra,gcp}/app/` via `sync_framework_map.py`.
  **Already shipped:** 8 AI-family framework packs (NIST AI RMF, 
  NIST AI 600-1, ISO 42001, EU AI Act, SOC 2 + AI, OWASP LLM Top 10, 
  OWASP Agentic, MITRE ATLAS) + ~20 security/industry packs. 13 
  mapping rules in the `rules[]` block, including 
  `ai_signin_personal_tier_controls` tagging that check_id against 
  NIST AI RMF GOVERN 3.2/6.1, EU AI Act Article 9/26, OWASP LLM02:2025, 
  MITRE ATLAS AML.T0057.
- **framework_meta.py** — DUPLICATED across `ai_summary/` and 
  `compliance_summary/` (same dict, different copies). Genuine drift 
  risk; consolidation is in scope for this slice.
- **AI scanner detectors** — `ai_scanner/detectors/` has 10 files: 
  agentic_workflow, mcp_server, embedding, prompt, vector_db, 
  model_usage, secrets_in_ai_code, correlator, crossdomain, framework.
- **AI inventory entity kinds** — `scanner_core/framework_registry.py:29-40` 
  lists `_AI_RESOURCE_KINDS`: bedrock_model, bedrock_guardrail, 
  sagemaker_*, comprehend_endpoint, azure_openai_deployment, 
  vertex_endpoint, ai_saas_app, ai_user_signin, ai_agent, ai_embedding, 
  ai_framework, ai_mcp_server, ai_model, ai_prompt, ai_tool, ai_vector_db.
- **Bedrock today** — inventory side exists (kind=`bedrock_model`); 
  runtime detection does NOT exist (`grep -rn 'bedrock' platform/lambda/event_router/` 
  returns empty).
- **AI compliance UI** — `web/src/routes/AISummary.tsx` renders 
  Exposure Score + Fail/Partial/Pass tiles + Source tiles + AI-family 
  framework tiles (via `compliance_summary` + `ai_family_meta()`). 
  No existing AI-BOM export route.
- **Last migration** — `platform/sql/015_mcp_connectors.sql`. Next is 016.

**What's genuinely new in this slice:**
1. Google Workspace scanner (new Fargate Lambda, OAuth, 4 detectors)
2. AWS Bedrock InvokeModel runtime detector (extension to event_router)
3. AI-BOM CycloneDX-ML export (new Lambda)
4. ~8 mapping rules added to existing registry
5. framework_meta consolidation
6. Migration 016 — tenant_workspace_oauth table
```

---

## 1. Goal and success criteria
[Standard section — restate the goal, list 5-8 testable success criteria.]

## 2. Why this design (and what was reconsidered)
[Standard section — design rationale + rejected alternatives.]

## 3. Scope (in scope / out of scope)
[Standard section — must align with §0's "what's genuinely new" list.]

## 4. Components & architecture
[Standard section — diagram + per-component summaries.]

## 5+. [Per-component sections]
[Detector design, data model, UI, testing, risks, etc.]

## N. References
[Cross-refs to specs, external docs, libraries.]
