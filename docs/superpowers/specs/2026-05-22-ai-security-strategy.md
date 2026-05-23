# AI Security — Strategy Doc

> Companion to `CISOBrief-v2.md` (PRD), `HANDOFF.md` (state), and the
> per-sub-project design specs. Frames CISO Copilot's AI security
> approach end-to-end so each sub-project's spec doesn't have to
> re-argue the framing.
>
> Date: 2026-05-22
> Status: brainstorm-approved by KK on 2026-05-22; supersedes the
> implicit "five-layer Discover → Govern" framing used in earlier specs.

---

## 1. The strategy in one line

> **Visibility is the demo. Compliance is the close. MCP is the
> differentiator. Inline traffic is somebody else's product.**

Every product-shape decision below derives from that line.

## 2. Why this framing

A CISO worries about four AI things, all real, all reported in customer
conversations: (1) shadow AI / data leakage, (2) the safety of AI apps
the customer is building, (3) compliance / board pressure, and (4) the
visibility gap ("I don't even know what AI we use"). Each of those is
its own market with multiple well-funded specialists — Lasso / Prompt
Security / Witness / Harmonic on shadow AI; Lakera / Robust Intelligence
/ Protect AI on app safety; every GRC vendor on compliance; Wiz / Orca /
Prisma on AI-SPM.

CISO Copilot's existing product shape — cloud-side scanner + chat front
door + multi-cloud + OSS-wrapped — is a strong fit for **(3) compliance
+ (4) visibility** and a poor fit for (1) and (2). Inline traffic
interception (browser ext / network proxy / endpoint agent) is the
shape that solves (1) and (2); it is a different product, and the
strategy doesn't try to be that product. When prospects name Netskope /
Zscaler / a comparable inline tool, **we promise integration**, we
don't build the gateway.

Inside the wedge of (3) + (4), CISO Copilot has one technical
differentiator nobody is leading on yet: **MCP discovery and risk
scoring**. MCP servers are unsigned binaries with tool-use authority,
invisible to every existing security control, and growing fast in
engineer workflows. Being first-out on "we discover and risk-score
your MCP estate" gives the product a novelty hook that compliance
roll-up alone cannot.

## 3. Where the AI estate lives — and what we can reach

The catalog below names every source where AI can appear in a customer's
estate, and for each: whether CISO Copilot can see it with the current
product shape, with planned extensions, or only by changing shape.

| Surface | What it shows | Reachable? |
|---|---|---|
| Code (GitHub / GitLab) | SDK calls, models referenced, keys leaked | ✅ Shipped (Slice 1a/1b) |
| AWS cloud-AI | Bedrock, SageMaker, AI-bearing Lambdas | ✅ Shipped |
| Azure cloud-AI | Azure OpenAI, Cognitive Services | 🔨 Planned — AI Visibility v2 |
| GCP cloud-AI | Vertex AI, Gemini endpoints | 🔨 Planned — AI Visibility v2 |
| Entra (Azure AD) sign-ins | Who signed into ChatGPT/Claude/Cursor | 🔨 Planned — AI Visibility v2 |
| MCP in code | `.mcp.json`, `@modelcontextprotocol/sdk` consumers | 🔮 Planned — MCP Risk sub-project |
| MCP in cloud | MCP servers deployed as Lambda / ECS / Container Apps | 🔮 Planned — MCP Risk sub-project |
| MCP risk catalog | Publisher / tool-surface / CVE enrichment | 🔮 Open-source effort — MCP Risk sub-project |
| MCP via MDM | Intune / Jamf / Kandji reading engineer-laptop configs | 🔮 Future slice within MCP Risk |
| OpenAI / Anthropic admin API | Projects, members, keys, models on provider side | 🚫 Blocked — admin-key access |
| Network gateway (Netskope, Zscaler) | Inline AI traffic visibility | 🔮 Promise integration when asked |
| Browser extension / endpoint agent | Per-session AI use on personal devices | 🚫 Out of shape |
| Microsoft 365 Copilot usage | Per-user Copilot interactions | 🚫 Microsoft surfaces this natively; defer |
| Google Workspace audit logs | Gemini / Workspace AI feature usage | 🚫 Deferred until M365-equivalent path proves out |

## 4. Sub-project map

| Sub-project | Status | Covers |
|---|---|---|
| **AI Discovery — Code (Slice 1a/1b)** | ✅ Shipped | GitHub code scanner, 8 detectors, AI Inventory on web + iOS |
| **AI Discovery — Cloud (AWS leg)** | ✅ Shipped | Cloud-AI pass inside `shasta_runner` (AWS) |
| **AI Visibility v2** | 🔨 Spec'd 2026-05-22 | Azure cloud-AI + Entra sign-ins + unified `/ai` view + per-person grouping + compliance mapping sweep (NIST AI RMF, ISO 42001, SOC 2 AI, EU AI Act) |
| **GCP-AI Discovery** | 📌 Reserved | Vertex AI / Gemini endpoint / Document AI / etc. detection. Shasta has no `gcp/ai_*` module today — sub-project's first decision is build-in-CISO-Copilot vs contribute-to-Shasta-upstream. Brainstormed after AI Visibility v2. |
| **MCP Risk** | 📌 Reserved | MCP in code + MCP in cloud + curated risk catalog + MDM-based discovery. Brainstormed after AI Visibility v2 + GCP-AI. |
| **Provider connectors (OpenAI / Anthropic)** | 🚫 Blocked | Admin-API path waits for an Enterprise customer; the `ai_connections` schema already permits the providers, so wiring the route is small once access lands. |

## 5. Guiding principles (unchanged from prior specs, restated for context)

- **AI is a lens, not a silo.** Every AI source writes into the unified
  `entities` / `edges` / `findings` tables; "AI-ness" is expressed in
  `kind`/`domain`; AI frameworks ride the same `findings.frameworks`
  rollup as SOC 2 / CIS / FedRAMP. No parallel surface.
- **Lean on OSS.** Wrap Shasta (cloud + AI checks), Whitney
  (prompt-injection Semgrep rules), Trivy/Semgrep/gitleaks for code +
  container surfaces. Don't reinvent detection engines.
- **Cloud-side scanner is the shape.** Inline traffic / endpoint /
  browser-extension shapes are out. Promise gateway integration when
  asked.
- **Slice vertically.** Each slice ships something demoable
  end-to-end; phases are vertical (DB + service + API + UI), not
  horizontal layers.
- **Sub-projects in series, not parallel.** Each sub-project gets its
  own design spec and implementation plan cycle; brainstorm before
  building.

## 6. Demo arc the strategy produces

1. **Today (already deployable):** "Here's all the AI we found in your
   code and your AWS cloud, scored against your existing posture."
2. **After AI Visibility v2:** "...and your Azure cloud, your GCP cloud,
   and everyone in your org who signed into ChatGPT / Claude / Cursor
   via Entra in the last 30 days. All mapped to NIST AI RMF, ISO
   42001, SOC 2 AI, EU AI Act."
3. **After MCP Risk:** "...and every MCP server in your code, your
   cloud, and (with MDM) your engineer laptops — risk-scored against
   our curated catalog."
4. **Asked-for integrations only:** Netskope / Zscaler / OpenAI admin
   API land when a real prospect names them as the deal-blocker.

## 7. What this strategy explicitly does NOT do

- Does not build an AI firewall.
- Does not build a browser extension or endpoint agent.
- Does not build prompt-content DLP.
- Does not build policy enforcement (block / allow / quarantine). The
  product reports; it does not enforce.
- Does not pre-build provider-side connectors against unverified APIs.
- Does not chase OWASP-LLM-Top-10 application-attack coverage as a
  feature wedge — Whitney + Garak + community tools handle this in
  the Assess module if and when a customer asks.

---

## Decisions log (this brainstorm)

| # | Decision | Rationale |
|---|---|---|
| S1 | Visibility + Compliance is the wedge; not shadow-AI DLP, not AI-app safety | Existing product shape; entrenched competitors in the other two; mid-market buyer reality |
| S2 | MCP discovery is the novelty hook inside that wedge | Nobody else is leading on this; reuses existing scanner shape; large adjacency to NPM supply-chain story |
| S3 | Inline-traffic shape (browser ext / network proxy / endpoint agent) is out of scope | Wrong product shape; saturated market; promise gateway integration instead |
| S4 | OpenAI / Anthropic admin-API connectors are deferred, not killed | Schema already permits them; only access is blocked |
| S5 | M365 Copilot usage ingest is out | Microsoft surfaces this natively in the M365 admin centre; revisit only if a prospect explicitly asks |
| S6 | Identity model is email-as-a-string, no graph resolver | "Good enough for now"; defers `human`-entity work until UX needs cross-source navigation |
| S7 | Strategy doc + focused spec, not one mega-spec | Brainstorm scoping decision; MCP gets its own brainstorm |
| S8 | SOC 2 AI controls join NIST AI RMF + ISO 42001 + EU AI Act in the compliance registry | KK ask, customer-relevant |

---

*This strategy doc lives alongside the per-sub-project design specs.
Update when a load-bearing strategy decision changes — otherwise let
the sub-project specs evolve independently.*
