// web/src/chat/artifacts/ApprovalCard.tsx
// ApprovalCard — full visual state machine for pending/editing/approved/cancelled/error.
// Phase 4d: approve→POST→PATCH, edit→PATCH, cancel→PATCH wired here.

import { useState, useCallback, Fragment } from "react";
import { api } from "../../lib/api";
import * as chatApi from "../chatApi";

interface EditField {
  key:      string;
  label:    string;
  type:     "text" | "textarea" | "select" | "date";
  options?: string[];
}

interface ApprovalCardProps {
  kind:            "approval_card";
  action_kind:     "add_risk" | "draft_policy";
  current_status:  "pending" | "editing" | "approved" | "cancelled" | "error";
  /** Stable UUID generated at propose-time — used as source_approval_id. */
  approval_id?:    string;
  payload:         Record<string, unknown>;
  edit_fields:     EditField[];
  result?:         { id: string; href: string };
  error?:          string;
  /** IDs needed to persist card-state changes back to the conversation. */
  conversationId?: string;
  messageId?:      string;
}

const ACTION_LABELS: Record<string, string> = {
  add_risk:     "Add Risk",
  draft_policy: "Draft Policy",
};

// Valid template keys for the policies endpoint.
const VALID_TEMPLATE_KEYS = new Set([
  "access_control", "incident_response", "data_classification",
  "vendor_management", "change_management", "security_awareness",
  "bcp_dr", "vulnerability_mgmt",
]);

function PayloadSummary({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload).filter(([, v]) => v !== "" && v != null);
  if (entries.length === 0) return null;
  return (
    <dl style={{ margin: 0, display: "grid",
                 gridTemplateColumns: "max-content 1fr",
                 gap: "4px 12px", fontSize: 12 }}>
      {entries.map(([k, v]) => (
        <Fragment key={k}>
          <dt style={{ color: "#A89B89", textTransform: "capitalize" }}>
            {k.replace(/_/g, " ")}
          </dt>
          <dd style={{ color: "#3A342B", margin: 0, fontWeight: 500 }}>
            {String(v)}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

function EditForm({
  fields, initial, onSave, onCancel,
}: {
  fields:    EditField[];
  initial:   Record<string, unknown>;
  onSave:    (data: Record<string, unknown>) => void;
  onCancel:  () => void;
}) {
  const [form, setForm] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const f of fields) out[f.key] = String(initial[f.key] ?? "");
    return out;
  });

  function set(key: string, val: string) {
    setForm(prev => ({ ...prev, [key]: val }));
  }

  const inputBase: React.CSSProperties = {
    width: "100%", boxSizing: "border-box",
    fontSize: 12, color: "#3A342B",
    background: "#FAF8F3", border: "1px solid #E8DFD0",
    borderRadius: 6, padding: "5px 8px",
    outline: "none", fontFamily: "inherit",
  };

  return (
    <div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {fields.map(f => (
          <div key={f.key}>
            <label style={{ display: "block", fontSize: 11, color: "#7A7268",
                            marginBottom: 3, textTransform: "capitalize" }}>
              {f.label}
            </label>
            {f.type === "textarea" ? (
              <textarea
                value={form[f.key]}
                onChange={e => set(f.key, e.target.value)}
                rows={4}
                style={{ ...inputBase, resize: "vertical" }}
              />
            ) : f.type === "select" && f.options ? (
              <select
                value={form[f.key]}
                onChange={e => set(f.key, e.target.value)}
                style={inputBase}
              >
                <option value="">— select —</option>
                {f.options.map(o => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            ) : (
              <input
                type={f.type === "date" ? "date" : "text"}
                value={form[f.key]}
                onChange={e => set(f.key, e.target.value)}
                style={inputBase}
              />
            )}
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button
          onClick={() => onSave(form)}
          style={{
            fontSize: 12, fontWeight: 600, padding: "6px 14px",
            borderRadius: 6, border: "none", cursor: "pointer",
            background: "#3A342B", color: "#FAF8F3",
          }}
        >
          Save
        </button>
        <button
          onClick={onCancel}
          style={{
            fontSize: 12, padding: "6px 14px", borderRadius: 6,
            border: "1px solid #E8DFD0", cursor: "pointer",
            background: "transparent", color: "#7A7268",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

export function ApprovalCard({
  kind: _kind,
  action_kind,
  current_status,
  approval_id,
  payload: initialPayload,
  edit_fields,
  result: initialResult,
  error: initialError,
  conversationId,
  messageId,
}: ApprovalCardProps) {
  const actionLabel = ACTION_LABELS[action_kind] ?? action_kind;

  // ── Local state ────────────────────────────────────────────────────────────
  // Drive from current_status (persisted). Local viewState tracks pending↔editing
  // without re-persisting on every toggle.
  type Status = "pending" | "editing" | "approved" | "cancelled" | "error";
  const [status, setStatus]     = useState<Status>(current_status);
  const [viewState, setViewState] = useState<"pending" | "editing">(
    current_status === "editing" ? "editing" : "pending",
  );
  const [payload, setPayload]   = useState(initialPayload);
  const [result, setResult]     = useState(initialResult);
  const [errorMsg, setErrorMsg] = useState(initialError ?? "");
  const [loading, setLoading]   = useState(false);

  // ── Persist helper ─────────────────────────────────────────────────────────
  const persist = useCallback(async (newContent: Record<string, unknown>) => {
    if (!conversationId || !messageId) return;
    try {
      await chatApi.patchMessage(conversationId, messageId, newContent);
    } catch (e) {
      console.error("ApprovalCard: patchMessage failed", e);
    }
  }, [conversationId, messageId]);

  // Build the full hint content for persisting (mirrors the original tool message shape)
  function buildHintContent(overrides: Partial<{
    current_status: Status;
    payload: Record<string, unknown>;
    result: { id: string; href: string } | undefined;
    error: string;
  }>) {
    const hint = {
      kind:           "approval_card" as const,
      action_kind,
      approval_id,
      current_status: overrides.current_status ?? status,
      payload:        overrides.payload        ?? payload,
      edit_fields,
      result:         overrides.result         ?? result,
      error:          overrides.error          ?? errorMsg,
    };
    // Wrap in the tool message content envelope used by MessageStream/toolHints
    return { _artifact_hint: hint };
  }

  // ── Approve ────────────────────────────────────────────────────────────────
  async function handleApprove() {
    if (status === "approved") return;   // double-click guard
    if (loading) return;
    setLoading(true);
    setErrorMsg("");
    try {
      let createdId: string;
      if (action_kind === "add_risk") {
        const body: Parameters<typeof api.createRisk>[0] = {
          title:              String(payload.title    ?? ""),
          severity:           String(payload.severity ?? "medium"),
          description:        payload.description ? String(payload.description) : undefined,
          owner:              payload.owner       ? String(payload.owner)       : undefined,
          due_date:           payload.due_date    ? String(payload.due_date)    : undefined,
          source_approval_id: approval_id,
        };
        const resp = await api.createRisk(body);
        createdId = resp.risk_id;
      } else {
        // draft_policy: use template_id as template_key (or fallback to access_control)
        const rawKey = String(payload.template_id ?? "");
        const template_key = VALID_TEMPLATE_KEYS.has(rawKey) ? rawKey : "access_control";
        const body: Parameters<typeof api.createPolicy>[0] = {
          template_key,
          vars:               {},
          title:              payload.name    ? String(payload.name)    : undefined,
          content_md:         payload.content ? String(payload.content) : undefined,
          source_approval_id: approval_id,
        } as any;
        const resp = await api.createPolicy(body);
        createdId = resp.policy_id;
      }

      const href = action_kind === "add_risk" ? "/risks" : "/policies";
      const newResult = { id: createdId, href };
      setResult(newResult);
      setStatus("approved");
      await persist(buildHintContent({ current_status: "approved", result: newResult }));
    } catch (e: any) {
      const msg = e?.message ?? "Unknown error";
      setErrorMsg(msg);
      setStatus("error");
    } finally {
      setLoading(false);
    }
  }

  // ── Edit / Save ────────────────────────────────────────────────────────────
  function handleEdit() {
    setViewState("editing");
  }

  async function handleSave(data: Record<string, unknown>) {
    setPayload(data);
    setViewState("pending");
    await persist(buildHintContent({ payload: data }));
  }

  // ── Cancel ─────────────────────────────────────────────────────────────────
  async function handleCancel() {
    setStatus("cancelled");
    await persist(buildHintContent({ current_status: "cancelled" }));
  }

  // ── Retry (from error) ─────────────────────────────────────────────────────
  function handleRetry() {
    setStatus("pending");
    setViewState("pending");
    setErrorMsg("");
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (status === "approved") {
    return (
      <div style={{
        background: "#F0FDF4",
        border: "1px solid #BBF7D0",
        borderRadius: 12, padding: 16, margin: "8px 0",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8,
                      fontFamily: "Georgia, serif", fontSize: 15, color: "#065F46" }}>
          <span>✓</span>
          <span>{actionLabel} approved</span>
        </div>
        {result?.href && (
          <a
            href={result.href}
            target="_blank"
            rel="noopener noreferrer"
            style={{ display: "inline-block", marginTop: 8, fontSize: 12,
                     color: "#065F46", textDecoration: "underline" }}
          >
            View {result.id} →
          </a>
        )}
      </div>
    );
  }

  if (status === "cancelled") {
    return (
      <div style={{
        background: "#FFFCF6",
        border: "1px solid #E8DFD0",
        borderRadius: 12, padding: 16, margin: "8px 0",
        opacity: 0.6,
      }}>
        <div style={{ fontSize: 13, color: "#7A7268",
                      textDecoration: "line-through", fontStyle: "italic" }}>
          {actionLabel} — cancelled
        </div>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div style={{
        background: "#FFFCF6",
        border: "1px solid #FECACA",
        borderRadius: 12, padding: 16, margin: "8px 0",
      }}>
        <div style={{ fontFamily: "Georgia, serif", fontSize: 15,
                      color: "#B91C1C", marginBottom: 6 }}>
          {actionLabel} failed
        </div>
        {errorMsg && (
          <div style={{ fontSize: 12, color: "#5A1E1E", background: "#FEF2F2",
                        borderRadius: 6, padding: "6px 10px", marginBottom: 10 }}>
            {errorMsg}
          </div>
        )}
        <button
          onClick={handleRetry}
          style={{
            fontSize: 12, fontWeight: 600, padding: "6px 14px",
            borderRadius: 6, border: "none", cursor: "pointer",
            background: "#D85F3B", color: "#FAF8F3",
          }}
        >
          Retry
        </button>
      </div>
    );
  }

  if (viewState === "editing") {
    return (
      <div style={{
        background: "#FFFCF6",
        border: "1px solid #E8DFD0",
        borderRadius: 12, padding: 16, margin: "8px 0",
      }}>
        <div style={{ fontSize: 12, color: "#7A7268", fontWeight: 600,
                      textTransform: "uppercase", letterSpacing: "0.05em",
                      marginBottom: 12 }}>
          Edit · {actionLabel}
        </div>
        <EditForm
          fields={edit_fields}
          initial={payload}
          onSave={handleSave}
          onCancel={() => setViewState("pending")}
        />
      </div>
    );
  }

  // ── Pending (default) ──────────────────────────────────────────────────────
  return (
    <div style={{
      background: "#FFFCF6",
      border: "1px solid #E8DFD0",
      borderRadius: 12, padding: 16, margin: "8px 0",
    }}>
      <div style={{ display: "flex", alignItems: "baseline",
                    justifyContent: "space-between", marginBottom: 10 }}>
        <div style={{ fontSize: 12, color: "#7A7268", fontWeight: 600,
                      textTransform: "uppercase", letterSpacing: "0.05em" }}>
          {actionLabel} · pending approval
        </div>
        <span style={{
          fontSize: 10, color: "#85613A", background: "#F5E8DB",
          borderRadius: 4, padding: "2px 6px",
        }}>
          action
        </span>
      </div>

      <div style={{ marginBottom: 14 }}>
        <PayloadSummary payload={payload} />
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          onClick={handleApprove}
          disabled={loading}
          style={{
            fontSize: 12, fontWeight: 600, padding: "6px 14px",
            borderRadius: 6, border: "none",
            cursor: loading ? "not-allowed" : "pointer",
            background: loading ? "#9E9488" : "#3A342B", color: "#FAF8F3",
          }}
        >
          {loading ? "Approving…" : "Approve"}
        </button>
        <button
          onClick={handleEdit}
          disabled={loading}
          style={{
            fontSize: 12, padding: "6px 14px", borderRadius: 6,
            border: "1px solid #E8DFD0", cursor: loading ? "not-allowed" : "pointer",
            background: "transparent", color: "#3A342B",
          }}
        >
          Edit
        </button>
        <button
          onClick={handleCancel}
          disabled={loading}
          style={{
            fontSize: 12, padding: "6px 14px", borderRadius: 6,
            border: "1px solid #E8DFD0", cursor: loading ? "not-allowed" : "pointer",
            background: "transparent", color: "#7A7268",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
