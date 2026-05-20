// web/src/chat/SourceSideSheet.tsx
// Right-side panel that opens when a CitationChip or EntityList row is clicked.
// Listens for the window "open-source-sheet" CustomEvent whose detail is a Source.
// Fetches the underlying record (entity or finding) and renders key fields.
// Warm Quiet Paper palette: #FFFCF6 surface, #E8DFD0 left border, #3A342B text.

import { useEffect, useRef, useState } from "react";
import type { Source } from "./tools";
import { api } from "../lib/api";
import type { EntityDetail, Finding } from "../lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FetchedRecord =
  | { kind: "entity";  data: EntityDetail }
  | { kind: "finding"; data: Finding }
  | { kind: "raw";     data: Source };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** A single labelled row in the detail table. */
function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div style={{ display: "flex", gap: 10, padding: "6px 0",
                  borderBottom: "1px solid #F0E8DB", alignItems: "flex-start" }}>
      <span style={{ fontSize: 11, color: "#A89B89", fontWeight: 600,
                     textTransform: "uppercase", letterSpacing: "0.04em",
                     minWidth: 110, flexShrink: 0, paddingTop: 2 }}>
        {label}
      </span>
      <span style={{ fontSize: 13, color: "#3A342B", flex: 1,
                     wordBreak: "break-word" }}>
        {value}
      </span>
    </div>
  );
}

/** Severity pill — mirrors the palette used in FindingCard / RiskCard. */
const SEV_COLORS: Record<string, { bg: string; text: string }> = {
  critical: { bg: "#FDECEA", text: "#C62828" },
  high:     { bg: "#FFF3E0", text: "#E65100" },
  medium:   { bg: "#FFFDE7", text: "#F9A825" },
  low:      { bg: "#F1F8E9", text: "#558B2F" },
  info:     { bg: "#E3F2FD", text: "#1565C0" },
};

function SeverityPill({ sev }: { sev: string }) {
  const c = SEV_COLORS[sev] ?? { bg: "#F5F0E6", text: "#7A7268" };
  return (
    <span style={{ fontSize: 11, fontWeight: 600, background: c.bg, color: c.text,
                   borderRadius: 6, padding: "2px 8px", textTransform: "capitalize" }}>
      {sev}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Content renderers
// ---------------------------------------------------------------------------

function EntityContent({ data }: { data: EntityDetail }) {
  const attrs = data.attributes ?? {};
  const attrEntries = Object.entries(attrs).slice(0, 12); // cap at 12 to avoid overwhelming
  return (
    <div>
      <Row label="Kind"         value={data.kind} />
      <Row label="Display name" value={data.display_name} />
      <Row label="Natural key"  value={data.natural_key} />
      <Row label="Domain"       value={data.domain} />
      {data.source_path && <Row label="Source path" value={data.source_path} />}
      <Row label="First seen"   value={data.first_seen_at ? new Date(data.first_seen_at).toLocaleString() : null} />
      <Row label="Last seen"    value={data.last_seen_at  ? new Date(data.last_seen_at).toLocaleString()  : null} />
      {attrEntries.length > 0 && (
        <>
          <div style={{ fontSize: 11, color: "#A89B89", fontWeight: 600,
                        textTransform: "uppercase", letterSpacing: "0.04em",
                        margin: "12px 0 4px" }}>
            Attributes
          </div>
          {attrEntries.map(([k, v]) => (
            <Row key={k} label={k}
              value={typeof v === "object" ? JSON.stringify(v, null, 2) : String(v)} />
          ))}
        </>
      )}
      {data.evidence_packet && (
        <>
          <div style={{ fontSize: 11, color: "#A89B89", fontWeight: 600,
                        textTransform: "uppercase", letterSpacing: "0.04em",
                        margin: "12px 0 4px" }}>
            Evidence packet
          </div>
          <pre style={{ fontSize: 11, color: "#7A7268", background: "#F5F0E6",
                        borderRadius: 8, padding: 10, overflowX: "auto",
                        whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {JSON.stringify(data.evidence_packet, null, 2)}
          </pre>
        </>
      )}
    </div>
  );
}

function FindingContent({ data }: { data: Finding }) {
  const frameworks = data.frameworks ? Object.keys(data.frameworks) : [];
  return (
    <div>
      <Row label="Severity"     value={<SeverityPill sev={data.severity} />} />
      <Row label="Title"        value={data.title} />
      <Row label="Check ID"     value={data.check_id} />
      <Row label="Status"       value={data.status} />
      <Row label="Domain"       value={data.domain} />
      {data.description && <Row label="Description" value={data.description} />}
      {data.resource_arn  && <Row label="Resource"    value={data.resource_arn} />}
      {data.resource_type && <Row label="Type"        value={data.resource_type} />}
      {data.region        && <Row label="Region"      value={data.region} />}
      {data.remediation   && <Row label="Remediation" value={data.remediation} />}
      {frameworks.length > 0 && (
        <Row label="Frameworks"
          value={frameworks.join(", ")} />
      )}
      <Row label="First seen" value={data.first_seen ? new Date(data.first_seen).toLocaleString() : null} />
      <Row label="Last seen"  value={data.last_seen  ? new Date(data.last_seen).toLocaleString()  : null} />
    </div>
  );
}

function RawContent({ data }: { data: Source }) {
  const entries = Object.entries(data).filter(([, v]) => v !== undefined && v !== null);
  return (
    <div>
      {entries.map(([k, v]) => (
        <Row key={k} label={k} value={String(v)} />
      ))}
      {entries.length === 0 && (
        <div style={{ fontSize: 13, color: "#A89B89" }}>No source details available.</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SourceSideSheet() {
  const [open,    setOpen]    = useState(false);
  const [source,  setSource]  = useState<Source | null>(null);
  const [loading, setLoading] = useState(false);
  const [record,  setRecord]  = useState<FetchedRecord | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Listen for open-source-sheet events.
  // A cancellation guard prevents a stale fetch (fired by a previous event) from
  // overwriting state after a newer event has already taken over, and also prevents
  // setState calls on an unmounted component.
  useEffect(() => {
    let cancelled = false;

    function handleEvent(ev: Event) {
      const src = (ev as CustomEvent<Source>).detail;
      if (!src) return;
      setSource(src);
      setOpen(true);
      setRecord(null);
      setLoading(true);
      cancelled = false; // reset for this invocation

      // Fetch the underlying record
      (async () => {
        try {
          if (src.entity_id) {
            const data = await api.getEntity(src.entity_id);
            if (cancelled) return;
            setRecord({ kind: "entity", data });
          } else if (src.finding_id) {
            // No /findings/{id} endpoint — use the list + find pattern (same as getFinding tool)
            const broader = await api.listFindings({ limit: 100 });
            if (cancelled) return;
            const found = broader.findings.find(f => f.finding_id === src.finding_id);
            if (found) {
              setRecord({ kind: "finding", data: found });
            } else {
              setRecord({ kind: "raw", data: src });
            }
          } else {
            if (cancelled) return;
            setRecord({ kind: "raw", data: src });
          }
        } catch (err) {
          if (cancelled) return;
          console.error("SourceSideSheet fetch failed", err);
          setRecord({ kind: "raw", data: src });
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
    }

    window.addEventListener("open-source-sheet", handleEvent);
    return () => {
      cancelled = true;
      window.removeEventListener("open-source-sheet", handleEvent);
    };
  }, []);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    function handleKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(ev: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(ev.target as Node)) {
        setOpen(false);
      }
    }
    // mousedown (not capture) — the panelRef.contains() guard filters out
    // inside-panel clicks, so outside clicks are what close the sheet.
    window.addEventListener("mousedown", handleClick);
    return () => window.removeEventListener("mousedown", handleClick);
  }, [open]);

  if (!open) return null;

  // Panel title based on what we fetched
  let headerTitle = "Source";
  if (record?.kind === "entity")  headerTitle = record.data.display_name || "Entity";
  else if (record?.kind === "finding") headerTitle = record.data.title || "Finding";
  else if (source?.entity_id)   headerTitle = "Entity";
  else if (source?.finding_id)  headerTitle = "Finding";

  return (
    // Backdrop — full-viewport overlay so outside clicks close the sheet
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        // semi-transparent scrim
        background: "rgba(58, 52, 43, 0.18)",
      }}
    >
      {/* Panel */}
      <div
        ref={panelRef}
        style={{
          position: "absolute", top: 0, right: 0, bottom: 0,
          width: 420,
          background: "#FFFCF6",
          borderLeft: "3px solid #E8DFD0",
          boxShadow: "-4px 0 24px rgba(58,52,43,0.10)",
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
        }}
      >
        {/* Header */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "16px 20px", borderBottom: "1px solid #E8DFD0",
          position: "sticky", top: 0, background: "#FFFCF6", zIndex: 1,
        }}>
          <div style={{ fontWeight: 700, fontSize: 15, color: "#3A342B",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        flex: 1, marginRight: 8 }}>
            {headerTitle}
          </div>
          <button
            aria-label="Close"
            onClick={() => setOpen(false)}
            style={{
              background: "none", border: "none", cursor: "pointer",
              fontSize: 18, color: "#A89B89", padding: "2px 6px",
              borderRadius: 6, flexShrink: 0,
              lineHeight: 1,
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = "#F0E8DB"; }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = "none"; }}
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: "16px 20px", flex: 1 }}>
          {loading ? (
            <div style={{ fontSize: 13, color: "#A89B89", textAlign: "center",
                          marginTop: 40 }}>
              Loading…
            </div>
          ) : record ? (
            <>
              {record.kind === "entity"  && <EntityContent  data={record.data} />}
              {record.kind === "finding" && <FindingContent data={record.data} />}
              {record.kind === "raw"     && <RawContent     data={record.data} />}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
