import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { connectVoice, type VoiceConnection } from "./connection";
import { buildFunctionCallOutput, buildResponseCreate, type RealtimeServerEvent } from "./events";
import { executeTool } from "./tools";

type Status = "idle" | "connecting" | "connected" | "listening" | "thinking" | "speaking" | "error";

interface Line {
  id: string;
  who: "user" | "assistant";
  text: string;
  partial?: boolean;
}

export function VoiceChat({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [status, setStatus]       = useState<Status>("idle");
  const [transcript, setTranscript] = useState<Line[]>([]);
  const [error, setError]         = useState<string | null>(null);

  const connRef       = useRef<VoiceConnection | null>(null);
  const assistantText = useRef<Map<string, string>>(new Map());
  const fnCallArgs    = useRef<Map<string, { name: string; args: string }>>(new Map());

  // Response-lifecycle tracking. When a tool finishes faster than the model's
  // in-flight response, sending response.create immediately fails with
  // "Conversation already has an active response in progress". So we queue.
  const responseActive    = useRef(false);
  const pendingResponseCreate = useRef(false);

  // viewActions hands the tools layer a handle to mutate the frontend
  // (navigate, etc.) — this is the "voice changes the dashboard" trick.
  const viewActions = { navigate: (p: string) => nav(p) };

  useEffect(() => () => connRef.current?.close(), []);

  async function start() {
    setStatus("connecting");
    setError(null);
    try {
      const conn = await connectVoice({
        onEvent: (e) => handleEvent(e),
        onConnectionStateChange: (s) => {
          if (s === "failed" || s === "closed" || s === "disconnected") {
            setStatus("error");
            setError(`Connection ${s}`);
          }
        },
      });
      connRef.current = conn;
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function stop() {
    connRef.current?.close();
    connRef.current = null;
    setStatus("idle");
  }

  async function handleEvent(event: RealtimeServerEvent) {
    const conn = connRef.current;
    if (!conn) return;

    switch (event.type) {
      case "session.created":
        setStatus("connected");
        break;
      case "response.created":
        responseActive.current = true;
        break;
      case "input_audio_buffer.speech_started":
        setStatus("listening");
        break;
      case "input_audio_buffer.speech_stopped":
        setStatus("thinking");
        break;
      case "conversation.item.input_audio_transcription.completed": {
        const e = event as { item_id: string; transcript: string };
        setTranscript((t) => [...t, { id: `user-${e.item_id}`, who: "user", text: e.transcript }]);
        break;
      }
      case "response.output_audio_transcript.delta": {
        const e = event as { item_id: string; delta: string };
        const cur = assistantText.current.get(e.item_id) ?? "";
        const next = cur + e.delta;
        assistantText.current.set(e.item_id, next);
        const id = `assistant-${e.item_id}`;
        setTranscript((t) => {
          if (t.find((l) => l.id === id)) {
            return t.map((l) => (l.id === id ? { ...l, text: next, partial: true } : l));
          }
          return [...t, { id, who: "assistant", text: next, partial: true }];
        });
        setStatus("speaking");
        break;
      }
      case "response.output_audio_transcript.done": {
        const e = event as { item_id: string; transcript: string };
        const id = `assistant-${e.item_id}`;
        setTranscript((t) => t.map((l) => (l.id === id ? { ...l, text: e.transcript, partial: false } : l)));
        break;
      }
      case "response.function_call_arguments.delta": {
        const e = event as { call_id: string; delta: string };
        const cur = fnCallArgs.current.get(e.call_id) ?? { name: "", args: "" };
        cur.args = cur.args + e.delta;
        fnCallArgs.current.set(e.call_id, cur);
        break;
      }
      case "response.function_call_arguments.done": {
        const e = event as { call_id: string; name: string; arguments: string };
        const args = (() => { try { return JSON.parse(e.arguments || "{}"); } catch { return {}; } })();
        let result: unknown;
        try {
          result = await executeTool(e.name, args, viewActions);
        } catch (err) {
          result = { error: err instanceof Error ? err.message : String(err) };
        }
        // Always safe to send the tool output. Defer the response.create until
        // we see response.done for the current response — otherwise OpenAI 400s
        // with "Conversation already has an active response in progress".
        conn.send(buildFunctionCallOutput(e.call_id, JSON.stringify(result)));
        if (responseActive.current) {
          pendingResponseCreate.current = true;
        } else {
          conn.send(buildResponseCreate());
        }
        // If the model navigated us away, close the voice modal so the
        // destination is visible — the voice can resume from the new view.
        if (typeof result === "object" && result && "navigated_to" in result) {
          setTimeout(onClose, 400);
        }
        break;
      }
      case "response.done":
        responseActive.current = false;
        setStatus("connected");
        if (pendingResponseCreate.current) {
          pendingResponseCreate.current = false;
          conn.send(buildResponseCreate());
        }
        break;
      case "error": {
        const e = event as { error: { message: string } };
        setError(e.error.message);
        setStatus("error");
        break;
      }
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-end md:items-center justify-center p-0 md:p-6 z-50">
      <div className="bg-white rounded-t-2xl md:rounded-2xl shadow-xl w-full max-w-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between p-4 border-b border-slate-200">
          <div>
            <h2 className="font-semibold">Voice chat</h2>
            <p className="text-xs text-slate-500 mt-0.5">{statusLabel(status)}</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-lg">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {transcript.length === 0 ? (
            <p className="text-slate-500 text-sm text-center py-12">
              Press the mic to start. Ask things like “what are my top risks?” or “add a high-severity risk to investigate SSO MFA”.
            </p>
          ) : (
            transcript.map((line) => (
              <div
                key={line.id}
                className={`flex ${line.who === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[80%] px-3.5 py-2 rounded-2xl text-sm ${
                    line.who === "user"
                      ? "bg-blue-600 text-white"
                      : "bg-slate-100 text-slate-800"
                  }`}
                >
                  {line.text}
                </div>
              </div>
            ))
          )}
        </div>

        {error && (
          <div className="mx-4 mb-2 p-3 rounded-lg bg-red-50 text-red-700 text-xs">{error}</div>
        )}

        <div className="p-4 border-t border-slate-200 flex justify-center">
          {status === "idle" || status === "error" ? (
            <button
              onClick={start}
              className="w-16 h-16 rounded-full bg-blue-600 hover:bg-blue-700 text-white flex items-center justify-center shadow-lg transition"
              aria-label="Start voice"
            >
              <MicIcon />
            </button>
          ) : (
            <button
              onClick={stop}
              className="w-16 h-16 rounded-full bg-red-600 hover:bg-red-700 text-white flex items-center justify-center shadow-lg transition"
              aria-label="Stop voice"
            >
              <StopIcon />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function statusLabel(s: Status): string {
  return {
    idle:       "Press the mic to start",
    connecting: "Connecting…",
    connected:  "Connected — speak",
    listening:  "Listening…",
    thinking:   "Thinking…",
    speaking:   "Speaking…",
    error:      "Error",
  }[s];
}

function MicIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="26" height="26">
      <path d="M12 14a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3z" />
      <path d="M19 11a1 1 0 10-2 0 5 5 0 11-10 0 1 1 0 10-2 0 7 7 0 006 6.92V21h-2a1 1 0 100 2h6a1 1 0 100-2h-2v-3.08A7 7 0 0019 11z" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}
