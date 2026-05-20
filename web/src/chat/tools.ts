// web/src/chat/tools.ts
// Single source of truth for the 12-tool shared catalog (SP4 §6.1).
//
// Two translators emit per-LLM schemas:
//   toAnthropicTools()  → Anthropic Messages .tools array
//   toRealtimeTools()   → OpenAI Realtime session.tools array
//
// executeTool(name, args) dispatches to the matching tool.

import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// Source + ArtifactHint types — spec §6.3
// These are the source of truth; Task 4b.2 builds the 8 artifact components
// against these types.
// ---------------------------------------------------------------------------

export type Source = {
  entity_id?:          string;
  finding_id?:         string;
  evidence_packet_id?: string;
  scan_id?:            string;
  last_scan_at?:       string;
};

export type ArtifactHint =
  | {
      kind:      "kpi_card";
      label:     string;
      value:     string;
      detail?:   string;
      severity?: "critical" | "high" | "medium" | "low" | "info";
      tags?:     string[];
      source?:   Source;
    }
  | {
      kind:     "entity_list";
      title?:   string;
      entities: Array<{
        id:           string;
        kind:         string;
        display_name: string;
        source_path?: string;
        source?:      Source;
      }>;
    }
  | {
      kind:          "finding_card";
      finding_id:    string;
      check_id:      string;
      title:         string;
      severity:      "critical" | "high" | "medium" | "low" | "info";
      description?:  string;
      resource_arn?: string;
      region?:       string;
      frameworks?:   string[];
      source:        Source;
    }
  | {
      kind:      "risk_card";
      risk_id:   string;
      title:     string;
      severity:  "critical" | "high" | "medium" | "low" | "info";
      // mirrors the risks.status DB CHECK constraint
      status:    "open" | "mitigated" | "accepted" | "transferred" | "closed";
      owner?:    string;
      due_date?: string;
      source?:   Source;
    }
  | {
      kind:     "chart_bar";
      title:    string;
      x_label?: string;
      y_label?: string;
      series:   Array<{ label: string; value: number; color?: string }>;
      source?:  Source;
    }
  | {
      kind:      "chart_donut";
      title:     string;
      segments:  Array<{ label: string; value: number; color?: string }>;
      source?:   Source;
    }
  | {
      kind:         "severity_breakdown";
      total:        number;
      critical:     number;
      high:         number;
      medium:       number;
      low:          number;
      delta_since?: string;
      source?:      Source;
    }
  | {
      kind:           "approval_card";
      action_kind:    "add_risk" | "draft_policy";
      current_status: "pending" | "editing" | "approved" | "cancelled" | "error";
      /** Stable UUID generated at propose-time; used as source_approval_id for idempotent creates. */
      approval_id?:   string;
      payload:        Record<string, unknown>;
      edit_fields:    Array<{
        key:      string;
        label:    string;
        type:     "text" | "textarea" | "select" | "date";
        options?: string[];
      }>;
      result?: { id: string; href: string };
      error?:  string;
    };

// ---------------------------------------------------------------------------
// Tool interface
// ---------------------------------------------------------------------------

export type Flavor = "data" | "action" | "side-effect";

export interface ToolResult {
  result:            unknown;
  _artifact_hint?:   ArtifactHint;    // primary (single-artifact tools)
  _artifact_hints?:  ArtifactHint[];  // all artifacts (multi-artifact tools)
  source?:           Source;
}

export interface Tool {
  name:         string;
  description:  string;
  input_schema: object;   // JSON Schema for args
  flavor:       Flavor;
  execute:      (args: any) => Promise<ToolResult>;
}

// ---------------------------------------------------------------------------
// Static edit_fields per action_kind  (spec §8)
// ---------------------------------------------------------------------------

type EditField = {
  key: string; label: string;
  type: "text" | "textarea" | "select" | "date";
  options?: string[];
};

const ADD_RISK_EDIT_FIELDS: EditField[] = [
  { key: "title",    label: "Title",    type: "text" },
  { key: "severity", label: "Severity", type: "select", options: ["critical", "high", "medium", "low"] },
  { key: "status",   label: "Status",   type: "select", options: ["open", "mitigated", "accepted", "transferred", "closed"] },
  { key: "owner",    label: "Owner",    type: "text" },
  { key: "due_date", label: "Due date", type: "date" },
];

const DRAFT_POLICY_EDIT_FIELDS: EditField[] = [
  { key: "name",    label: "Name",    type: "text" },
  { key: "content", label: "Content", type: "textarea" },
  { key: "status",  label: "Status",  type: "select", options: ["draft", "approved", "retired"] },
];

// ---------------------------------------------------------------------------
// Severity ordering helper
// ---------------------------------------------------------------------------

const SEV_ORDER: Array<"critical" | "high" | "medium" | "low" | "info"> =
  ["critical", "high", "medium", "low", "info"];

// ---------------------------------------------------------------------------
// Tool definitions (12)
// ---------------------------------------------------------------------------

/**
 * get_morning_briefing
 * Fans out to /findings/summary + /risks.
 * Artifacts: kpi_card (top finding) + severity_breakdown + kpi_card (risk register).
 * All three hints are packed into result._artifact_hints for the renderer to paint.
 */
const getMorningBriefing: Tool = {
  name:        "get_morning_briefing",
  description: "Returns a morning security briefing: the top open finding severity, a severity breakdown of all findings, and a risk register summary. Use on first sign-in or when the user asks for a daily overview.",
  flavor:      "data",
  input_schema: {
    type: "object", properties: {}, required: [],
  },
  async execute(_args): Promise<ToolResult> {
    const [summaryData, risksData] = await Promise.all([
      api.findingsSummary(),
      api.listRisks(),
    ]);

    const { by_severity, total } = summaryData;

    const topSev = SEV_ORDER.find(s => by_severity[s] > 0) ?? null;

    const topFindingKpi: ArtifactHint = {
      kind:     "kpi_card",
      label:    "Top open finding",
      value:    topSev
        ? `${by_severity[topSev]} ${topSev} finding${by_severity[topSev] !== 1 ? "s" : ""}`
        : "No open findings",
      severity: topSev ?? "info",
      detail:   `${total} total findings`,
    };

    const severityBreakdown: ArtifactHint = {
      kind:     "severity_breakdown",
      total,
      critical: by_severity.critical,
      high:     by_severity.high,
      medium:   by_severity.medium,
      low:      by_severity.low,
    };

    const { risks, count } = risksData;
    const openCount = risks.filter(r => r.status === "open").length;
    const riskKpi: ArtifactHint = {
      kind:   "kpi_card",
      label:  "Risk register",
      value:  `${count} total risk${count !== 1 ? "s" : ""}`,
      detail: `${openCount} open`,
    };

    return {
      result: {
        summary:     summaryData,
        risks_count: count,
        open_risks:  openCount,
      },
      _artifact_hint:  topFindingKpi,
      _artifact_hints: [topFindingKpi, severityBreakdown, riskKpi],
    };
  },
};

/**
 * query_entities
 * Uses api.listEntities(). Returns entity_list.
 */
const queryEntities: Tool = {
  name:        "query_entities",
  description: "Query the entity inventory. Filter by domain (ai, cloud, repo, identity, asm), kind (e.g. aws_s3_bucket, ai_model), or repo name. Returns an entity_list artifact.",
  flavor:      "data",
  input_schema: {
    type: "object",
    properties: {
      domain:   { type: "string", enum: ["ai", "cloud", "repo", "identity", "asm"] },
      kind:     { type: "string" },
      repo:     { type: "string", description: "GitHub repo full name filter" },
      page:     { type: "number" },
      per_page: { type: "number", default: 20 },
    },
    required: [],
  },
  async execute(args): Promise<ToolResult> {
    const data = await api.listEntities({
      domain:   args.domain,
      kind:     args.kind,
      repo:     args.repo,
      page:     args.page,
      per_page: args.per_page ?? 20,
    });
    const hint: ArtifactHint = {
      kind:     "entity_list",
      title:    args.domain ? `${args.domain} entities` : "Entities",
      entities: data.entities.map(e => ({
        id:           e.id,
        kind:         e.kind,
        display_name: e.display_name,
        source_path:  e.source_path ?? undefined,
        source:       { entity_id: e.id },
      })),
    };
    return { result: data, _artifact_hint: hint };
  },
};

/**
 * get_entity
 * Uses api.getEntity(id). Returns entity_list of one (renders detail).
 */
const getEntity: Tool = {
  name:        "get_entity",
  description: "Get a single entity by its UUID. Returns an entity_list artifact with one entry.",
  flavor:      "data",
  input_schema: {
    type: "object",
    properties: {
      entity_id: { type: "string", description: "UUID of the entity" },
    },
    required: ["entity_id"],
  },
  async execute(args): Promise<ToolResult> {
    const e = await api.getEntity(args.entity_id);
    const hint: ArtifactHint = {
      kind:     "entity_list",
      title:    e.display_name,
      entities: [{
        id:           e.id,
        kind:         e.kind,
        display_name: e.display_name,
        source_path:  e.source_path ?? undefined,
        source:       { entity_id: e.id },
      }],
    };
    return {
      result:         e,
      _artifact_hint: hint,
      source:         { entity_id: e.id },
    };
  },
};

/**
 * query_findings
 * Uses api.listFindings(). Returns entity_list (>3 results) or finding_card (≤3).
 * For ≤3 results, result._artifact_hints carries all cards; _artifact_hint is first.
 */
const queryFindings: Tool = {
  name:        "query_findings",
  description: "Query open security findings. Filter by severity, cloud, or check_id. Returns an entity_list for many results or individual finding_card artifacts for ≤3 matches.",
  flavor:      "data",
  input_schema: {
    type: "object",
    properties: {
      severity: { type: "string", enum: ["critical", "high", "medium", "low", "info"] },
      cloud:    { type: "string", enum: ["aws", "azure", "gcp", "entra"] },
      check_id: { type: "string" },
      limit:    { type: "number", default: 20 },
    },
    required: [],
  },
  async execute(args): Promise<ToolResult> {
    const data = await api.listFindings({
      severity: args.severity,
      cloud:    args.cloud,
      check_id: args.check_id,
      limit:    args.limit ?? 20,
    });
    const { findings } = data;

    if (findings.length > 0 && findings.length <= 3) {
      const cards: ArtifactHint[] = findings.map(f => ({
        kind:         "finding_card" as const,
        finding_id:   f.finding_id,
        check_id:     f.check_id,
        title:        f.title,
        severity:     f.severity,
        description:  f.description ?? undefined,
        resource_arn: f.resource_arn ?? undefined,
        region:       f.region ?? undefined,
        frameworks:   f.frameworks ? Object.keys(f.frameworks) : undefined,
        source:       { finding_id: f.finding_id },
      }));
      return {
        result:          data,
        _artifact_hint:  cards[0],
        _artifact_hints: cards,
        source:          { finding_id: findings[0].finding_id },
      };
    }

    // entity_list for 0 or >3 results
    const hint: ArtifactHint = {
      kind:     "entity_list",
      title:    args.severity ? `${args.severity} findings` : "Findings",
      entities: findings.map(f => ({
        id:           f.finding_id,
        kind:         "finding",
        display_name: f.title,
        source:       { finding_id: f.finding_id },
      })),
    };
    return { result: data, _artifact_hint: hint };
  },
};

/**
 * get_finding
 * Uses api.listFindings() with check_id or a broader fetch to match finding_id.
 * Note: /findings/{id} endpoint not present in api.ts; we use list + filter.
 * Returns finding_card.
 */
const getFinding: Tool = {
  name:        "get_finding",
  description: "Get a single finding by finding_id or check_id. Returns a finding_card artifact with full detail.",
  flavor:      "data",
  input_schema: {
    type: "object",
    properties: {
      finding_id: { type: "string", description: "UUID of the finding" },
      check_id:   { type: "string", description: "Check ID (returns first match)" },
    },
    required: [],
  },
  async execute(args): Promise<ToolResult> {
    // Prefer check_id lookup (more selective)
    const byCheck = args.check_id
      ? await api.listFindings({ check_id: args.check_id, limit: 10 })
      : null;

    let finding = byCheck?.findings[0] ?? null;

    // If searching by finding_id and didn't match via check_id, scan a broader page
    if (args.finding_id && (!finding || finding.finding_id !== args.finding_id)) {
      const broader = await api.listFindings({ limit: 100 });
      finding = broader.findings.find(f => f.finding_id === args.finding_id) ?? finding;
    }

    if (!finding) {
      return { result: null };
    }

    const hint: ArtifactHint = {
      kind:         "finding_card",
      finding_id:   finding.finding_id,
      check_id:     finding.check_id,
      title:        finding.title,
      severity:     finding.severity,
      description:  finding.description ?? undefined,
      resource_arn: finding.resource_arn ?? undefined,
      region:       finding.region ?? undefined,
      frameworks:   finding.frameworks ? Object.keys(finding.frameworks) : undefined,
      source:       { finding_id: finding.finding_id },
    };
    return {
      result:         finding,
      _artifact_hint: hint,
      source:         { finding_id: finding.finding_id },
    };
  },
};

/**
 * get_compliance_summary
 * Uses api.complianceSummary(). Returns chart_donut + kpi_card per framework.
 * result._artifact_hints contains [donut, ...frameworkKpis].
 */
const getComplianceSummary: Tool = {
  name:        "get_compliance_summary",
  description: "Returns a compliance summary across all connected frameworks. Renders as a donut chart plus per-framework score tiles.",
  flavor:      "data",
  input_schema: {
    type: "object", properties: {}, required: [],
  },
  async execute(_args): Promise<ToolResult> {
    const data = await api.complianceSummary();
    const { summary } = data;

    const segments = Object.entries(summary).map(([framework, counts]) => ({
      label: framework,
      value: counts.passing,
      color: counts.score_pct >= 80 ? "#4CAF50"
           : counts.score_pct >= 50 ? "#FF9800"
           : "#F44336",
    }));

    const donut: ArtifactHint = {
      kind:     "chart_donut",
      title:    "Compliance posture",
      segments,
    };

    const frameworkKpis: ArtifactHint[] = Object.entries(summary).map(([framework, counts]) => ({
      kind:     "kpi_card" as const,
      label:    framework,
      value:    `${counts.score_pct.toFixed(0)}%`,
      detail:   `${counts.passing} / ${counts.total} controls passing`,
      severity: (counts.score_pct >= 80 ? "info"
               : counts.score_pct >= 50 ? "medium"
               : "high") as "critical" | "high" | "medium" | "low" | "info",
    }));

    return {
      result:          data,
      _artifact_hint:  donut,
      _artifact_hints: [donut, ...frameworkKpis],
    };
  },
};

/**
 * get_severity_breakdown
 * Uses api.findingsSummary(). Returns severity_breakdown.
 */
const getSeverityBreakdown: Tool = {
  name:        "get_severity_breakdown",
  description: "Returns a count of findings by severity (critical, high, medium, low). Use for posture overview or finding distribution questions.",
  flavor:      "data",
  input_schema: {
    type: "object", properties: {}, required: [],
  },
  async execute(_args): Promise<ToolResult> {
    const data = await api.findingsSummary();
    const { by_severity, total } = data;
    const hint: ArtifactHint = {
      kind:     "severity_breakdown",
      total,
      critical: by_severity.critical,
      high:     by_severity.high,
      medium:   by_severity.medium,
      low:      by_severity.low,
    };
    return { result: data, _artifact_hint: hint };
  },
};

/**
 * list_risks
 * Uses api.listRisks(). Returns risk_card list.
 * Maps api Risk.status → ArtifactHint risk_card.status.
 */
const listRisks: Tool = {
  name:        "list_risks",
  description: "List items in the risk register. Filter by status (open/mitigated/accepted/transferred/closed) or severity.",
  flavor:      "data",
  input_schema: {
    type: "object",
    properties: {
      status:   { type: "string", enum: ["open", "mitigated", "accepted", "transferred", "closed"] },
      severity: { type: "string", enum: ["critical", "high", "medium", "low", "info"] },
    },
    required: [],
  },
  async execute(args): Promise<ToolResult> {
    const data = await api.listRisks({ status: args.status, severity: args.severity });
    const { risks } = data;

    // risk_card.status mirrors the risks.status DB CHECK constraint:
    // 'open' | 'mitigated' | 'accepted' | 'transferred' | 'closed'
    // Pass r.status straight through — no lossy remapping needed.
    const cards: ArtifactHint[] = risks.map(r => ({
      kind:      "risk_card" as const,
      risk_id:   r.risk_id,
      title:     r.title,
      severity:  r.severity,
      status:    r.status as "open" | "mitigated" | "accepted" | "transferred" | "closed",
      owner:     r.owner ?? undefined,
      due_date:  r.due_date ?? undefined,
      source:    r.finding_id ? { finding_id: r.finding_id } : undefined,
    }));

    return {
      result:          data,
      _artifact_hint:  cards[0] as ArtifactHint | undefined,
      _artifact_hints: cards,
    };
  },
};

/**
 * propose_risk_entry
 * Action tool — returns a pending approval_card. NO mutation.
 */
const proposeRiskEntry: Tool = {
  name:        "propose_risk_entry",
  description: "Propose adding a new entry to the risk register. Returns an editable approval card that the user must explicitly approve — never auto-executes.",
  flavor:      "action",
  input_schema: {
    type: "object",
    properties: {
      title:       { type: "string" },
      severity:    { type: "string", enum: ["critical", "high", "medium", "low"] },
      description: { type: "string" },
      owner:       { type: "string" },
      due_date:    { type: "string", description: "YYYY-MM-DD" },
      status:      { type: "string", enum: ["open", "mitigated", "accepted", "transferred", "closed"], default: "open" },
    },
    required: ["title", "severity"],
  },
  async execute(args): Promise<ToolResult> {
    const hint: ArtifactHint = {
      kind:           "approval_card",
      action_kind:    "add_risk",
      current_status: "pending",
      approval_id:    crypto.randomUUID(),
      payload: {
        title:       args.title       ?? "",
        severity:    args.severity    ?? "medium",
        description: args.description ?? "",
        owner:       args.owner       ?? "",
        due_date:    args.due_date    ?? "",
        status:      args.status      ?? "open",
      },
      edit_fields: ADD_RISK_EDIT_FIELDS,
    };
    return {
      result:         { proposed: hint.payload },
      _artifact_hint: hint,
    };
  },
};

/**
 * propose_policy_draft
 * Action tool — returns a pending approval_card. NO mutation.
 */
const proposePolicyDraft: Tool = {
  name:        "propose_policy_draft",
  description: "Propose drafting a new policy document. Returns an editable approval card with a content field — the user must approve before any policy is created.",
  flavor:      "action",
  input_schema: {
    type: "object",
    properties: {
      name:        { type: "string" },
      content:     { type: "string", description: "Initial policy content in Markdown" },
      template_id: { type: "string", description: "Optional policy template key" },
      status:      { type: "string", enum: ["draft", "approved", "retired"], default: "draft" },
    },
    required: ["name"],
  },
  async execute(args): Promise<ToolResult> {
    const hint: ArtifactHint = {
      kind:           "approval_card",
      action_kind:    "draft_policy",
      current_status: "pending",
      approval_id:    crypto.randomUUID(),
      payload: {
        name:        args.name        ?? "",
        content:     args.content     ?? "",
        template_id: args.template_id ?? "",
        status:      args.status      ?? "draft",
      },
      edit_fields: DRAFT_POLICY_EDIT_FIELDS,
    };
    return {
      result:         { proposed: hint.payload },
      _artifact_hint: hint,
    };
  },
};

/**
 * navigate_to
 * Side-effect — returns intent only; Task 4b.3 wires actual navigation.
 */
const navigateTo: Tool = {
  name:        "navigate_to",
  description: "Navigate the user to a specific app route (e.g. /findings, /risks, /policies, /dashboard). Use when the user asks to go to a section.",
  flavor:      "side-effect",
  input_schema: {
    type: "object",
    properties: {
      path: { type: "string", description: "App route path, e.g. /findings" },
    },
    required: ["path"],
  },
  async execute(args): Promise<ToolResult> {
    return { result: { navigated_to: args.path as string } };
  },
};

/**
 * filter_findings_view
 * Side-effect — returns intent only; Task 4b.3 wires actual UI filter.
 */
const filterFindingsView: Tool = {
  name:        "filter_findings_view",
  description: "Apply filters to the findings view (severity, cloud, check_id, status). Use when the user says 'show only critical AWS findings'. Returns the filter intent; the caller applies it to the UI.",
  flavor:      "side-effect",
  input_schema: {
    type: "object",
    properties: {
      severity: { type: "string", enum: ["critical", "high", "medium", "low", "info"] },
      cloud:    { type: "string", enum: ["aws", "azure", "gcp", "entra"] },
      check_id: { type: "string" },
      status:   { type: "string" },
    },
    required: [],
  },
  async execute(args): Promise<ToolResult> {
    return { result: { filtered: args as Record<string, unknown> } };
  },
};

// ---------------------------------------------------------------------------
// Full catalog — 12 tools in spec order
// ---------------------------------------------------------------------------

export const TOOLS: Tool[] = [
  getMorningBriefing,
  queryEntities,
  getEntity,
  queryFindings,
  getFinding,
  getComplianceSummary,
  getSeverityBreakdown,
  listRisks,
  proposeRiskEntry,
  proposePolicyDraft,
  navigateTo,
  filterFindingsView,
];

// ---------------------------------------------------------------------------
// Translators
// ---------------------------------------------------------------------------

/** Emit Anthropic Messages tools array. */
export function toAnthropicTools(tools: Tool[]) {
  return tools.map(t => ({
    name:         t.name,
    description:  t.description,
    input_schema: t.input_schema,
  }));
}

/** Emit OpenAI Realtime session.tools array. */
export function toRealtimeTools(tools: Tool[]) {
  return tools.map(t => ({
    type:        "function" as const,
    name:        t.name,
    description: t.description,
    parameters:  t.input_schema,
  }));
}

// ---------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------

/** Dispatch a tool call by name. Throws if the name is unknown. */
export async function executeTool(name: string, args: any): Promise<ToolResult> {
  const tool = TOOLS.find(t => t.name === name);
  if (!tool) throw new Error(`Unknown tool: "${name}"`);
  return tool.execute(args);
}
