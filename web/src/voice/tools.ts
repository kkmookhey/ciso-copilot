// Tool relay for voice — maps Realtime tool calls to our authed REST API.
// The OpenAI Realtime session advertises these tools (see platform/lambda/
// voice_session/main.py _tools()). When the model invokes one, the browser
// receives `response.function_call_arguments.done`, executes here, and
// posts the result back via `conversation.item.create` + `response.create`.

import { api } from "../lib/api";

interface ToolArgs {
  [k: string]: unknown;
}

/// View-mutation actions the model can drive via tools. Implemented by the
/// VoiceChat host (Welcome/Shell) and passed in at executeTool time.
export interface ViewActions {
  navigate: (path: string) => void;
}

export async function executeTool(name: string, args: ToolArgs, view: ViewActions): Promise<unknown> {
  switch (name) {
    case "navigate_to": {
      const v = (args.view as string) || "";
      const path = {
        overview:       "/",
        findings:       "/findings",
        risks:          "/risks",
        policies:       "/policies",
        questionnaires: "/questionnaires",
        connect:        "/connect",
        admin:          "/admin",
      }[v];
      if (!path) return { error: "unknown_view", view: v };
      view.navigate(path);
      return { navigated_to: path };
    }
    case "filter_findings_view": {
      const params = new URLSearchParams();
      if (args.severity)  params.set("severity",  String(args.severity));
      if (args.cloud)     params.set("cloud",     String(args.cloud));
      if (args.framework) params.set("framework", String(args.framework));
      const qs = params.toString();
      const path = `/findings${qs ? "?" + qs : ""}`;
      view.navigate(path);
      return { navigated_to: path };
    }
    case "get_top_risks": {
      const limit    = (args.limit    as number) ?? 5;
      const severity = (args.severity as string) ?? "critical,high";
      const cloud    = args.cloud as string | undefined;
      const r = await api.listFindings({ severity, cloud, limit });
      return {
        total: r.total,
        findings: r.findings.map((f) => ({
          title: f.title, severity: f.severity, check_id: f.check_id,
          resource: f.resource_arn, region: f.region,
        })),
      };
    }
    case "list_connected_clouds": {
      const r = await api.listConnections();
      return {
        connections: r.connections.map((c) => ({
          cloud:   c.cloud_type, status: c.status,
          account: c.account_identifier, name: c.display_name,
        })),
      };
    }
    case "get_compliance_summary": {
      const r = await api.complianceSummary();
      return { summary: r.summary };
    }
    case "list_recent_alerts": {
      const limit    = (args.limit    as number) ?? 5;
      const severity = args.severity as string | undefined;
      const r = await api.listEvents({ severity, limit });
      return {
        total: r.total,
        events: r.events.map((e) => ({
          title: e.title, kind: e.kind, source: e.source,
          severity: e.severity, fired_at: e.fired_at,
        })),
      };
    }
    case "list_risks": {
      const status   = (args.status   as string) ?? "open";
      const severity = args.severity as string | undefined;
      const r = await api.listRisks({ status, severity });
      return {
        count: r.count,
        risks: r.risks.map((x) => ({
          title: x.title, severity: x.severity, status: x.status,
          owner: x.owner, due_date: x.due_date,
        })),
      };
    }
    case "add_risk": {
      const title       = args.title as string;
      const severity    = (args.severity    as string) ?? "medium";
      const description = args.description as string | undefined;
      const owner       = args.owner       as string | undefined;
      const due_date    = args.due_date    as string | undefined;
      if (!title) return { error: "title_required" };
      const r = await api.createRisk({ title, severity, description, owner, due_date });
      return { created: true, risk_id: r.risk_id };
    }
    default:
      return { error: "unknown_tool", name };
  }
}
