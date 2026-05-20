// web/src/chat/artifacts/ApprovalCard.tsx
// ApprovalCard — full visual state machine for pending/editing/approved/cancelled/error.
// Approve→POST wiring is Phase 4d. This task: full UI + inline edit form.
// The component manages its own pending↔editing view state locally.

import { useState, Fragment } from "react";

interface EditField {
  key:      string;
  label:    string;
  type:     "text" | "textarea" | "select" | "date";
  options?: string[];
}

interface ApprovalCardProps {
  kind:           "approval_card";
  action_kind:    "add_risk" | "draft_policy";
  current_status: "pending" | "editing" | "approved" | "cancelled" | "error";
  payload:        Record<string, unknown>;
  edit_fields:    EditField[];
  result?:        { id: string; href: string };
  error?:         string;
  // Phase 4d wires these:
  onApprove?:     (payload: Record<string, unknown>) => void;
  onEdit?:        () => void;
  onCancel?:      () => void;
  onSave?:        (payload: Record<string, unknown>) => void;
  onRetry?:       () => void;
}

const ACTION_LABELS: Record<string, string> = {
  add_risk:     "Add Risk",
  draft_policy: "Draft Policy",
};

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
  action_kind, current_status, payload, edit_fields, result, error,
  onApprove, onEdit, onCancel, onSave, onRetry,
}: ApprovalCardProps) {
  // Local view-state: allows toggling pending↔editing within the same card
  // without requiring the parent to re-render.
  const [viewState, setViewState] = useState<"pending" | "editing">(
    current_status === "editing" ? "editing" : "pending",
  );
  // Editable payload copy for the inline form
  const [editPayload, setEditPayload] = useState(payload);

  const actionLabel = ACTION_LABELS[action_kind] ?? action_kind;

  // ── Approved ──────────────────────────────────────────────────────────────
  if (current_status === "approved") {
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

  // ── Cancelled ─────────────────────────────────────────────────────────────
  if (current_status === "cancelled") {
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

  // ── Error ─────────────────────────────────────────────────────────────────
  if (current_status === "error") {
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
        {error && (
          <div style={{ fontSize: 12, color: "#5A1E1E", background: "#FEF2F2",
                        borderRadius: 6, padding: "6px 10px", marginBottom: 10 }}>
            {error}
          </div>
        )}
        <button
          onClick={() => onRetry?.()}
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

  // ── Editing (local or current_status) ─────────────────────────────────────
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
          initial={editPayload}
          onSave={(data) => {
            setEditPayload(data);
            setViewState("pending");
            onSave?.(data);
          }}
          onCancel={() => {
            setViewState("pending");
          }}
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
        <PayloadSummary payload={editPayload} />
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          onClick={() => onApprove?.(editPayload)}
          style={{
            fontSize: 12, fontWeight: 600, padding: "6px 14px",
            borderRadius: 6, border: "none", cursor: "pointer",
            background: "#3A342B", color: "#FAF8F3",
          }}
        >
          Approve
        </button>
        <button
          onClick={() => {
            setViewState("editing");
            onEdit?.();
          }}
          style={{
            fontSize: 12, padding: "6px 14px", borderRadius: 6,
            border: "1px solid #E8DFD0", cursor: "pointer",
            background: "transparent", color: "#3A342B",
          }}
        >
          Edit
        </button>
        <button
          onClick={() => onCancel?.()}
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
