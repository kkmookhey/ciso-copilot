// ChatShell — auth-gated four-column chat surface.
// Replicates the auth + user-info pattern from routes/Shell.tsx:
//   1. isSignedIn() check → redirect /signin
//   2. api.me() → tenant status check → redirect /pending or signOut
//   3. render once email is known

import { useEffect, useReducer, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isSignedIn, signOut } from "../lib/cognito";
import { api, type MeResponse } from "../lib/api";
import { ModuleRail } from "./ModuleRail";
import { ConversationRail } from "./ConversationRail";
import { ChatCenter } from "./ChatCenter";
import { SourceSideSheet } from "./SourceSideSheet";
import { chatReducer, initialState } from "./state";
import * as chatApi from "./chatApi";
import { executeTool } from "./tools";

const ADMIN_EMAILS = new Set([
  "kkmookhey@gmail.com",
  "kkmookhey@transilience.ai",
  "kkmookhey@networkintelligence.ai",
]);
function isAdmin(email: string | null | undefined): boolean {
  return !!email && ADMIN_EMAILS.has(email.toLowerCase());
}

export function ChatShell() {
  const nav = useNavigate();
  const [me, setMe]         = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [state, dispatch]   = useReducer(chatReducer, initialState);
  const [convs, setConvs]   = useState<chatApi.ConversationSummary[]>([]);

  // --- Auth gate (mirrors routes/Shell.tsx) ---
  useEffect(() => {
    if (!isSignedIn()) { nav("/signin", { replace: true }); return; }
    api.me().then((r) => {
      setMe(r);
      if (r.tenant?.status === "pending")  { nav("/pending", { replace: true }); return; }
      if (r.tenant?.status === "rejected") { signOut(); return; }
      setLoading(false);
    }).catch(() => signOut());
  }, [nav]);

  // --- Chat boot: load/create initial conversation ---
  useEffect(() => {
    if (loading || !me) return;
    (async () => {
      const list = await chatApi.listConversations();
      setConvs(list);
      const recent = list[0];
      const within24h =
        recent &&
        Date.now() - new Date(recent.last_activity_at).getTime() < 86_400_000;
      if (within24h) {
        await openConversation(recent.id);
      } else {
        const id = await chatApi.createConversation();
        dispatch({ type: "load", id, title: "New conversation", messages: [] });
        setConvs(await chatApi.listConversations());

        // Fetch and display the morning briefing for fresh conversations only.
        // Wrapped in try/catch — a failed briefing must never break the landing.
        try {
          const result = await executeTool("get_morning_briefing", {});
          const content = {
            tool_name:       "get_morning_briefing",
            args:            {},
            result:          result.result,
            _artifact_hints: result._artifact_hints,
            source:          result.source,
          };
          dispatch({ type: "appendTool", content });
          // Persist server-side so it survives refresh
          await chatApi.appendMessage(id, "tool", content);
        } catch (err) {
          console.error("Morning briefing fetch failed — skipping:", err);
        }
      }
    })();
    // One-shot boot: runs when auth resolves (loading → false). openConversation
    // is recreated each render but intentionally omitted — the effect fires once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, me]);

  async function openConversation(id: string) {
    const c = await chatApi.getConversation(id);
    dispatch({ type: "load", id: c.id, title: c.title, messages: c.messages });
  }

  async function onNew() {
    const id = await chatApi.createConversation();
    dispatch({ type: "load", id, title: "New conversation", messages: [] });
    setConvs(await chatApi.listConversations());
  }

  async function onRename(id: string, title: string) {
    await chatApi.patchTitle(id, title);
    // Update the local list in-place — no round-trip needed
    setConvs((prev) => prev.map((c) => c.id === id ? { ...c, title } : c));
    // If this is the currently-open conversation, update the header title too
    if (state.conversationId === id) {
      dispatch({ type: "setTitle", title });
    }
  }

  async function onDelete(id: string) {
    await chatApi.deleteConversation(id);
    const remaining = convs.filter((c) => c.id !== id);
    setConvs(remaining);
    if (state.conversationId === id) {
      // Move to the next most-recent conversation, or create a fresh one
      if (remaining.length > 0) {
        await openConversation(remaining[0].id);
      } else {
        await onNew();
      }
    }
  }

  async function onSend(text: string) {
    if (!state.conversationId) return;
    dispatch({ type: "append", message: { role: "user",      content: { text } } });
    dispatch({ type: "append", message: { role: "assistant", content: { text: "" } } });
    dispatch({ type: "streaming", on: true });
    try {
      await chatApi.streamMessage(state.conversationId, text, {
        onDelta: (t) => dispatch({ type: "streamDelta", text: t }),
        onToolResult: (ev) => {
          // Skip side-effect tools — they carry no artifact and are handled
          // by onSideEffect below.
          if (ev.side_effect && !ev.artifact_hint && !ev.artifact_hints) return;
          dispatch({
            type: "appendTool",
            content: {
              tool_name:       ev.tool_name,
              _artifact_hint:  ev.artifact_hint,
              _artifact_hints: ev.artifact_hints,
              source:          ev.source,
            },
          });
        },
        onSideEffect: (toolName, intent) => {
          if (toolName === "navigate_to" && typeof intent.navigated_to === "string") {
            nav(intent.navigated_to);
          } else {
            // filter_findings_view and any future side-effect tools — full
            // behaviour lands later; a log keeps the stream non-crashing.
            console.log("chat side-effect", toolName, intent);
          }
        },
      });
    } finally {
      dispatch({ type: "streaming", on: false });
    }
  }

  if (loading) {
    return (
      <div style={{ minHeight: "100vh", display: "flex",
                    alignItems: "center", justifyContent: "center",
                    color: "#7A7268" }}>
        Loading…
      </div>
    );
  }

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <ModuleRail email={me?.user?.email ?? ""} isAdmin={isAdmin(me?.user?.email)} />
      <ConversationRail
        conversations={convs}
        activeId={state.conversationId}
        onSelect={openConversation}
        onNew={onNew}
        onRename={onRename}
        onDelete={onDelete}
      />
      <ChatCenter state={state} onSend={onSend} />
      <SourceSideSheet />
    </div>
  );
}
