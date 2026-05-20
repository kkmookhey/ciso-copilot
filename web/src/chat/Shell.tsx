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
import { chatReducer, initialState } from "./state";
import * as chatApi from "./chatApi";

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
      }
    })();
    // openConversation is stable; deps are correct
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

  async function onSend(text: string) {
    if (!state.conversationId) return;
    dispatch({ type: "append", message: { role: "user",      content: { text } } });
    dispatch({ type: "append", message: { role: "assistant", content: { text: "" } } });
    dispatch({ type: "streaming", on: true });
    try {
      await chatApi.streamMessage(
        state.conversationId,
        text,
        (t) => dispatch({ type: "streamDelta", text: t }),
      );
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
      <ModuleRail email={me?.user?.email ?? ""} />
      <ConversationRail
        conversations={convs}
        activeId={state.conversationId}
        onSelect={openConversation}
        onNew={onNew}
      />
      <ChatCenter state={state} onSend={onSend} />
    </div>
  );
}
