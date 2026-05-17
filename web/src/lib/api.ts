// API client for the CISO Copilot v2 platform.
// Auto-attaches Bearer <id_token> to every request via validIdToken().

import { validIdToken, signOut } from "./cognito";

const BASE_URL = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";

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
}

export interface Finding {
  finding_id:    string;
  check_id:      string;
  title:         string;
  description:   string | null;
  severity:      "critical" | "high" | "medium" | "low" | "info";
  status:        "fail" | "pass" | "not_assessed" | "not_applicable";
  resource_arn:  string | null;
  resource_type: string | null;
  region:        string | null;
  domain:        string;
  frameworks:    Record<string, string[]>;
  remediation:   string | null;
  first_seen:    string;
  last_seen:     string;
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

export const api = {
  me: ()                                      => call<MeResponse>("/me"),
  listConnections: ()                         => call<{ connections: Connection[] }>("/connections"),
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
  listFindings: (params?: { severity?: string; cloud?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.severity) q.set("severity", params.severity);
    if (params?.cloud)    q.set("cloud", params.cloud);
    if (params?.limit)    q.set("limit", String(params.limit));
    const qs = q.toString();
    return call<{ findings: Finding[]; limit: number; offset: number; count: number }>(
      `/findings${qs ? "?" + qs : ""}`,
    );
  },
};
