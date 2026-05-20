// web/src/chat/ChatCenter.tsx
import { MessageStream } from "./MessageStream";
import { Composer } from "./Composer";
import type { ChatState } from "./state";
import type { VoiceState } from "./voiceClient";

/** Persimmon breathing-dot keyframes injected once into the document. */
const BREATHING_STYLE = `
@keyframes ciso-breath {
  0%, 100% { box-shadow: 0 0 0 0px rgba(216,95,59,0.18); }
  50%       { box-shadow: 0 0 0 6px rgba(216,95,59,0.18); }
}
`;

let styleInjected = false;
function ensureBreathStyle() {
  if (styleInjected) return;
  const el = document.createElement("style");
  el.textContent = BREATHING_STYLE;
  document.head.appendChild(el);
  styleInjected = true;
}

export function ChatCenter({
  state,
  onSend,
  voiceState,
  onToggleVoice,
  syncWarning,
}: {
  state: ChatState;
  onSend: (t: string) => void;
  voiceState: VoiceState;
  onToggleVoice: () => void;
  syncWarning: boolean;
}) {
  if (voiceState === "on") ensureBreathStyle();

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column",
                  background: "#FAF8F3" }}>
      {/* Header */}
      <div style={{ padding: "14px 32px", borderBottom: "1px solid #E8DFD0",
                    fontFamily: "Georgia, serif", fontSize: 18,
                    color: "#3A342B", display: "flex", alignItems: "center",
                    gap: 10 }}>
        {state.title}
        {voiceState === "on" && (
          <span
            aria-label="Voice active"
            style={{
              display:        "inline-block",
              width:          10,
              height:         10,
              borderRadius:   "50%",
              background:     "#D85F3B",
              animation:      "ciso-breath 1.6s ease-in-out infinite",
              flexShrink:     0,
            }}
          />
        )}
      </div>

      {/* Sync-warning banner */}
      {syncWarning && (
        <div style={{
          background:   "#FFF4ED",
          borderBottom: "1px solid #F5C9A8",
          padding:      "8px 32px",
          fontSize:     13,
          color:        "#7A4020",
        }}>
          Transcript out of sync — refresh to recover.
        </div>
      )}

      <MessageStream messages={state.messages} conversationId={state.conversationId} />
      <Composer
        onSend={onSend}
        disabled={state.streaming}
        voiceState={voiceState}
        onToggleVoice={onToggleVoice}
      />
    </div>
  );
}
