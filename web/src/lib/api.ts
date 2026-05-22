// API client for the CISO Copilot v2 platform.
// Auto-attaches Bearer <id_token> to every request via validIdToken().

import { validIdToken, signOut } from "./cognito";

const BASE_URL = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";

export type ScanTier   = "quick" | "medium" | "deep";
export type ScanPhase  = "region_discovery" | "first_signal" | "crown_jewel" | "full" | "done";
export type ScanState  = "queued" | "running" | "partial" | "completed" | "failed";

export interface LatestScan {
  scan_id:    string;
  tier:       ScanTier;
  status:     ScanState;
  phase:      ScanPhase;
  started_at: string | null;
}

export interface ScanCoverageMap {
  tier?:    string;
  regions?: Record<string, { state: string; errors?: string[] }>;
}

export interface ScanStatus {
  scan_id:       string;
  tier:          ScanTier;
  status:        ScanState;
  phase:         ScanPhase;
  coverage_map:  ScanCoverageMap | null;
  started_at:    string | null;
  finished_at:   string | null;
  finding_count: number;
}

export interface MeResponse {
  user:   { email: string | null; role: string | null } | null;
  tenant: { tenant_id: string; display_name: string;
            status: "pending" | "approved" | "rejected" | "suspended" } | null;
}

export interface Connection {
  conn_id:            string;
  cloud_type:         "aws" | "azure" | "entra" | "gcp";
  display_name:       string;
  status:             "pending" | "active" | "error" | "revoked";
  account_identifier: string | null;
  signals:            { pull_scan?: boolean; alerts?: boolean; drift?: boolean };
  last_scan_at:       string | null;
  created_at:         string;
  latest_scan:        LatestScan | null;
}

export interface AlertEvent {
  event_id:     string;
  kind:         "alert" | "drift";
  source:       string;
  severity:     "critical" | "high" | "medium" | "low" | "info";
  title:        string;
  description:  string | null;
  resource_arn: string | null;
  actor:        string | null;
  fired_at:     string;
  ingested_at:  string;
}

export interface FindingGroup {
  domain:           string;
  check_id:         string;
  title:            string;
  check_title:      string;
  severity:         "critical" | "high" | "medium" | "low" | "info";
  count:            number;
  frameworks:       Record<string, string[]>;
  sample_resources: Array<{ resource_arn: string; region: string | null }>;
}

export interface Finding {
  finding_id:    string;
  check_id:      string;
  title:         string;
  check_title:   string;
  description:   string | null;
  severity:      "critical" | "high" | "medium" | "low" | "info";
  status:        "fail" | "partial" | "pass" | "not_assessed" | "not_applicable";
  resource_arn:  string | null;
  resource_type: string | null;
  region:        string | null;
  domain:        string;
  frameworks:    Record<string, string[]>;
  remediation:   string | null;
  first_seen:    string;
  last_seen:     string;
}

export interface AIConnection {
  id:              string;
  provider:        "github" | "openai" | "anthropic";
  status:          "pending" | "active" | "failed" | "revoked";
  github_org_name: string;
  created_at:      string;
}

export interface GitHubRepo {
  full_name:        string;
  default_branch:   string | null;
  last_pushed_at:   string | null;
  size_kb:          number | null;
  primary_language: string | null;
  is_private:       boolean;
}

export interface InstallUrlResponse {
  install_url: string;
}

export interface CompleteInstallResponse {
  connection_id: string;
}

export interface ListReposResponse {
  repos:       GitHubRepo[];
  next_page:   number | null;
  total_count: number;
}

export interface InitiateAwsResponse {
  connection_id: string;
  external_id:   string;
  cfn_url:       string;
  template_url:  string;
}

export interface InitiateAzureResponse {
  connection_id: string;
  external_id:   string;
  script_url:    string;
  run_command:   string;
}

export interface InitiateEntraResponse {
  connection_id: string;
  state:         string;
  consent_url:   string;
}

export interface InitiateGcpResponse {
  connection_id: string;
  external_id:   string;
  script_url:    string;
  run_command:   string;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await validIdToken();
  if (!token) { signOut(); throw new Error("not_signed_in"); }
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization:  `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  if (res.status === 401) { signOut(); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export interface ComplianceSummary {
  summary: Record<string, { total: number; passing: number; failing: number; score_pct: number }>;
  by_framework_control: Array<{ framework: string; control_id: string; fail_count: number; pass_count: number; total: number }>;
}

export interface TrustPageSettings {
  page_id?:             string;
  slug:                 string;
  public_name:          string;
  notes:                string | null;
  is_published:         boolean;
  show_compliance:      boolean;
  show_finding_counts:  boolean;
  show_clouds:          boolean;
  show_last_scan:       boolean;
  created_at?:          string;
  updated_at?:          string;
}

export interface QuestionnaireSummary {
  questionnaire_id: string;
  name:             string;
  template_key:     string;
  status:           "in_progress" | "complete" | "exported";
  created_at:       string;
  updated_at:       string;
  total:            number;
  answered:         number;
}

export interface QuestionnaireItem {
  item_id:        string;
  question_id:    string;
  question:       string;
  category:       string | null;
  answer:         string | null;        // "yes" | "no" | "partial" | free-text
  confidence:     string | null;        // "auto-high" | "auto-medium" | "manual" | "ai-suggested"
  evidence:       { check_ids?: string[]; pass?: number; fail?: number; note?: string };
  notes:          string | null;
  sort_order:     number;
  source_row_idx: number | null;        // for Excel write-back
}

export interface QuestionnaireDetail {
  questionnaire_id: string;
  name:             string;
  template_key:     string;
  status:           string;
  created_at:       string;
  source_filename:  string | null;
  items:            QuestionnaireItem[];
}

export interface PolicyTemplate {
  key:            string;
  title:          string;
  soc2_controls:  string[];
}

export interface PolicySummary {
  policy_id:     string;
  template_key:  string;
  title:         string;
  status:        "draft" | "approved" | "retired";
  version:       number;
  soc2_controls: string[];
  created_at:    string;
  updated_at:    string;
}

export interface Policy extends PolicySummary {
  content_md: string;
  vars:       Record<string, string>;
}

export interface Risk {
  risk_id:     string;
  title:       string;
  description: string | null;
  severity:    "critical" | "high" | "medium" | "low" | "info";
  status:      "open" | "mitigated" | "accepted" | "transferred" | "closed";
  owner:       string | null;
  due_date:    string | null;  // YYYY-MM-DD
  finding_id:  string | null;
  notes:       string | null;
  created_at:  string;
  updated_at:  string;
}

export interface AdminTenantRow {
  tenant_id:    string;
  display_name: string;
  email_domain: string;
  status:       "pending" | "approved" | "rejected" | "suspended";
  created_at:   string;
  first_user:   string | null;
}

export interface AIScanSummary {
  id:                              string;
  repo_full_name:                  string;
  status:                          "queued" | "running" | "success" | "failed";
  started_at:                      string;
  completed_at:                    string | null;
  error_message:                   string | null;
  assets_discovered_count:         number;
  relationships_discovered_count:  number;
  findings_generated_count:        number;
}

export type EntityKind =
  | "github_repo" | "ai_framework" | "ai_model" | "ai_mcp_server"
  | "ai_tool" | "ai_agent" | "ai_vector_db" | "ai_embedding" | "ai_prompt"
  | "aws_account" | "aws_s3_bucket" | "aws_iam_role" | "aws_iam_user"
  | "aws_lambda_function" | "aws_ec2_instance" | "aws_vpc" | "aws_subnet"
  | "aws_security_group";

export type EntityDomain = "ai" | "cloud" | "repo" | "identity" | "asm";

export interface EntitySummary {
  id:             string;
  kind:           EntityKind;
  natural_key:    string;
  display_name:   string;
  domain:         EntityDomain;
  source_path:    string | null;
  detector_id:    string;
  first_seen_at:  string;
  last_seen_at:   string;
  attributes:     Record<string, unknown>;
}

export interface EntityDetail extends EntitySummary {
  evidence_packet: Record<string, unknown> | null;
  connection_id:   string | null;
}

export interface EntityGraph {
  nodes: { data: { id: string; label: string; type: EntityKind; attributes: Record<string, unknown> } }[];
  edges: { data: { id: string; source: string; target: string; label: string } }[];
  meta:  { root_id: string; node_count: number; truncated: boolean };
}

export interface EntityRelationship {
  id:           string;
  kind:         string;
  direction:    "outgoing" | "incoming";
  other_entity: { id: string; kind: EntityKind; natural_key: string; display_name: string };
}

export interface FindingsSummary {
  by_status:   { fail: number; partial: number; pass: number };
  by_severity: { critical: number; high: number; medium: number; low: number; info: number };
  by_cloud:    { aws: number; azure: number; gcp: number; entra: number };
  total: number;
}

export const api = {
  me: ()                                      => call<MeResponse>("/me"),
  complianceSummary: ()                       => call<ComplianceSummary>("/compliance/summary"),
  findingsSummary: ()                         => call<FindingsSummary>("/findings/summary"),
  listRisks: (params?: { status?: string; severity?: string }) => {
    const q = new URLSearchParams();
    if (params?.status)   q.set("status", params.status);
    if (params?.severity) q.set("severity", params.severity);
    const qs = q.toString();
    return call<{ risks: Risk[]; count: number }>(`/risks${qs ? "?" + qs : ""}`);
  },
  createRisk: (body: {
    title: string; severity: string;
    description?: string; owner?: string; due_date?: string;
    finding_id?: string; notes?: string; source_approval_id?: string;
  }) => call<{ risk_id: string; status: string }>(`/risks`, {
    method: "POST", body: JSON.stringify(body),
  }),
  updateRisk: (riskId: string, body: { status?: string; owner?: string; due_date?: string | null; notes?: string }) =>
    call<{ updated: boolean }>(`/risks/${riskId}`, {
      method: "PATCH", body: JSON.stringify(body),
    }),
  listPolicyTemplates: () => call<{ templates: PolicyTemplate[] }>(`/policies/templates`),
  listPolicies: (status?: string) =>
    call<{ policies: PolicySummary[]; count: number }>(`/policies${status ? "?status=" + status : ""}`),
  getPolicy: (id: string) => call<Policy>(`/policies/${id}`),
  createPolicy: (body: {
    template_key: string;
    vars: Record<string, string>;
    source_approval_id?: string;
    /** Optional overrides — used by chat approval cards to preserve AI-authored content. */
    title?: string;
    content_md?: string;
  }) =>
    call<{ policy_id: string; status: string }>(`/policies`, {
      method: "POST", body: JSON.stringify(body),
    }),
  updatePolicy: (id: string, body: { content_md?: string; status?: string; title?: string }) =>
    call<{ updated: boolean }>(`/policies/${id}`, {
      method: "PATCH", body: JSON.stringify(body),
    }),
  enrichPolicy: (id: string) =>
    call<{ enriched: boolean; content_md: string }>(`/policies/${id}/enrich`, {
      method: "POST", body: "{}",
    }),
  generateAllPolicies: (vars: { company_name: string; effective_date: string; approver?: string }) =>
    call<{ count: number; policies: Array<{ template_key: string; policy_id: string; title: string; enriched: boolean; error?: string }> }>(
      `/policies/generate-all`, { method: "POST", body: JSON.stringify({ vars }) },
    ),
  listQuestionnaireTemplates: () =>
    call<{ templates: { key: string; name: string; question_count: number }[] }>(`/questionnaires/templates`),
  listQuestionnaires: () =>
    call<{ questionnaires: QuestionnaireSummary[] }>(`/questionnaires`),
  getQuestionnaire: (id: string) =>
    call<QuestionnaireDetail>(`/questionnaires/${id}`),
  createQuestionnaire: (body: { template_key: string; name?: string }) =>
    call<{ questionnaire_id: string; items: number }>(`/questionnaires`, {
      method: "POST", body: JSON.stringify(body),
    }),
  patchQuestionnaireItem: (qid: string, iid: string, body: { answer?: string | null; notes?: string }) =>
    call<{ updated: boolean }>(`/questionnaires/${qid}/items/${iid}`, {
      method: "PATCH", body: JSON.stringify(body),
    }),
  suggestQuestionnaireItem: (qid: string, iid: string) =>
    call<{ answer: string; justification: string; confidence: string }>(
      `/questionnaires/${qid}/items/${iid}`, { method: "POST", body: "{}" }
    ),
  questionnaireFromExcel: (body: { filename: string; name?: string; rows: Array<{ row_idx: number; question: string; category?: string }> }) =>
    call<{ questionnaire_id: string; items: number }>(
      `/questionnaires/from-excel`, { method: "POST", body: JSON.stringify(body) }
    ),
  getTrustPage: () => call<{ page: TrustPageSettings | null }>(`/trust`),
  putTrustPage: (body: Partial<TrustPageSettings>) =>
    call<{ saved: boolean; slug: string; is_published: boolean }>(`/trust`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  adminListTenants: (status: string = "pending") =>
    call<{ tenants: AdminTenantRow[] }>(`/admin/tenants?status=${encodeURIComponent(status)}`),
  adminTenantAction: (tenantId: string, decision: "approve" | "reject") =>
    call<{ tenant_id: string; new_status: string; notify_email: string | null; email_status: string }>(
      `/admin/tenants/${tenantId}/action`,
      { method: "POST", body: JSON.stringify({ decision }) },
    ),
  listConnections: ()                         => call<{ connections: Connection[] }>("/connections"),
  rescanConnection: (connId: string) =>
    call<{ scan_id: string; status: string }>(`/connections/${connId}/rescan`, {
      method: "POST", body: "{}",
    }),
  getScanStatus: (scanId: string) => call<ScanStatus>(`/scans/${scanId}`),
  deleteConnection: (connId: string) =>
    call<{ status: string }>(`/connections/${connId}`, { method: "DELETE" }),
  initiateAwsOnboarding: (displayName: string) =>
    call<InitiateAwsResponse>("/onboarding/aws/initiate", {
      method: "POST",
      body:   JSON.stringify({ display_name: displayName }),
    }),
  initiateAzureOnboarding: (displayName: string) =>
    call<InitiateAzureResponse>("/onboarding/azure/initiate", {
      method: "POST",
      body:   JSON.stringify({ display_name: displayName }),
    }),
  initiateEntraOnboarding: (displayName: string) =>
    call<InitiateEntraResponse>("/onboarding/entra/initiate", {
      method: "POST",
      body:   JSON.stringify({ display_name: displayName }),
    }),
  initiateGcpOnboarding: (displayName: string) =>
    call<InitiateGcpResponse>("/onboarding/gcp/initiate", {
      method: "POST",
      body:   JSON.stringify({ display_name: displayName }),
    }),
  listEvents: (params?: { kind?: string; severity?: string; source?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.kind)     q.set("kind", params.kind);
    if (params?.severity) q.set("severity", params.severity);
    if (params?.source)   q.set("source", params.source);
    if (params?.limit)    q.set("limit", String(params.limit));
    const qs = q.toString();
    return call<{ events: AlertEvent[]; total: number; limit: number; offset: number }>(
      `/events${qs ? "?" + qs : ""}`,
    );
  },
  listFindings: (params?: { severity?: string; cloud?: string; check_id?: string; status?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.severity) q.set("severity", params.severity);
    if (params?.cloud)    q.set("cloud", params.cloud);
    if (params?.check_id) q.set("check_id", params.check_id);
    if (params?.status)   q.set("status", params.status);
    if (params?.limit)    q.set("limit", String(params.limit));
    const qs = q.toString();
    return call<{ findings: Finding[]; limit: number; offset: number; count: number; total: number }>(
      `/findings${qs ? "?" + qs : ""}`,
    );
  },
  findingsRollup: (params?: { severity?: string; cloud?: string; q?: string }) => {
    const qs = new URLSearchParams();
    if (params?.severity) qs.set("severity", params.severity);
    if (params?.cloud)    qs.set("cloud", params.cloud);
    if (params?.q)        qs.set("q", params.q);
    const s = qs.toString();
    return call<{ groups: FindingGroup[]; total_findings: number; total_groups: number }>(
      `/findings/rollup${s ? "?" + s : ""}`,
    );
  },
  getGithubInstallUrl: () =>
    call<InstallUrlResponse>("/ai/connections/github/install_url", {
      method: "POST",
      body:   "{}",
    }),
  completeGithubInstall: (installationId: number, state: string) =>
    call<CompleteInstallResponse>("/ai/connections/github/complete", {
      method: "POST",
      body:   JSON.stringify({ installation_id: installationId, state }),
    }),
  listAIConnections: () =>
    call<{ connections: AIConnection[] }>("/ai/connections", { method: "GET" }),
  listAuthorizedRepos: (connectionId: string, page = 1) =>
    call<ListReposResponse>(
      `/ai/connections/${connectionId}/repos?page=${page}&per_page=30`,
      { method: "GET" },
    ),
  revokeAIConnection: (connectionId: string) =>
    call<void>(`/ai/connections/${connectionId}`, { method: "DELETE" }),
  startAIScan: (connectionId: string, repoFullName: string, defaultBranch?: string) =>
    call<{ scan_id: string }>("/ai/scans", {
      method: "POST",
      body: JSON.stringify({
        connection_id:  connectionId,
        repo_full_name: repoFullName,
        default_branch: defaultBranch ?? "main",
      }),
    }),
  listAIScans: (params?: { connection_id?: string; status?: string }) => {
    const q = new URLSearchParams();
    if (params?.connection_id) q.set("connection_id", params.connection_id);
    if (params?.status)        q.set("status", params.status);
    const qs = q.toString();
    return call<{ scans: AIScanSummary[] }>(`/ai/scans${qs ? "?" + qs : ""}`);
  },
  getAIScan: (scanId: string) => call<AIScanSummary>(`/ai/scans/${scanId}`),
  listEntities: (params?: { domain?: string; kind?: string; repo?: string; page?: number; per_page?: number }) => {
    const q = new URLSearchParams();
    if (params?.domain)   q.set("domain", params.domain);
    if (params?.kind)     q.set("kind", params.kind);
    if (params?.repo)     q.set("repo", params.repo);
    if (params?.page)     q.set("page", String(params.page));
    if (params?.per_page) q.set("per_page", String(params.per_page));
    const qs = q.toString();
    return call<{ entities: EntitySummary[]; next_page: number | null }>(`/entities${qs ? "?" + qs : ""}`);
  },
  getEntity:                (id: string) => call<EntityDetail>(`/entities/${id}`),
  getEntityGraph:           (id: string, depth = 4, maxNodes = 500) =>
    call<EntityGraph>(`/entities/${id}/graph?depth=${depth}&max_nodes=${maxNodes}`),
  getEntityRelationships:   (id: string, direction: "both" | "outgoing" | "incoming" = "both") =>
    call<{ relationships: EntityRelationship[] }>(`/entities/${id}/relationships?direction=${direction}`),
};
