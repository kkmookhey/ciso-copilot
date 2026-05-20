// web/src/chat/Composer.tsx
import { useState } from "react";
import type { VoiceState } from "./voiceClient";

/** Thin-stroke inline SVG mic icon. */
function MicIcon({ color }: { color: string }) {
  return (
    <svg
      width="18" height="18" viewBox="0 0 24 24"
      fill="none" stroke={color} strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true"
    >
      {/* microphone capsule */}
      <rect x="9" y="2" width="6" height="11" rx="3" />
      {/* stand */}
      <path d="M5 10a7 7 0 0 0 14 0" />
      {/* stem */}
      <line x1="12" y1="17" x2="12" y2="21" />
      {/* base */}
      <line x1="9"  y1="21" x2="15" y2="21" />
    </svg>
  );
}

export function Composer({
  onSend,
  disabled,
  voiceState,
  onToggleVoice,
}: {
  onSend:        (text: string) => void;
  disabled:      boolean;
  voiceState:    VoiceState;
  onToggleVoice: () => void;
}) {
  const [text, setText] = useState("");

  const voiceActive      = voiceState === "on";
  const voiceConnecting  = voiceState === "connecting";

  // Text input / send button are disabled during voice mode or when streaming.
  const textDisabled = disabled || voiceActive || voiceConnecting;

  const send = () => {
    if (text.trim() && !textDisabled) { onSend(text.trim()); setText(""); }
  };

  // Mic button appearance
  const micBg: React.CSSProperties["background"] = voiceActive
    ? "#D85F3B"
    : voiceConnecting
    ? "#F5E8DB"
    : "#F5F2EC";

  const micBorder = voiceActive
    ? "1px solid #D85F3B"
    : "1px solid #E8DFD0";

  const micIconColor = voiceActive
    ? "#FFFFFF"
    : voiceConnecting
    ? "#C8A88A"
    : "#7A7268";

  const micCursor: React.CSSProperties["cursor"] = voiceConnecting
    ? "default"
    : "pointer";

  return (
    <div style={{ display: "flex", gap: 8, padding: "16px 32px",
                  borderTop: "1px solid #E8DFD0", alignItems: "center" }}>
      {/* Mic toggle */}
      <button
        onClick={onToggleVoice}
        disabled={voiceConnecting}
        aria-label={voiceActive ? "Stop voice" : "Start voice"}
        aria-pressed={voiceActive}
        title={voiceActive ? "Stop voice" : voiceConnecting ? "Connecting…" : "Start voice"}
        style={{
          flexShrink:   0,
          width:        38,
          height:       38,
          borderRadius: "50%",
          border:       micBorder,
          background:   micBg,
          cursor:       micCursor,
          display:      "flex",
          alignItems:   "center",
          justifyContent: "center",
          transition:   "background 0.2s, border 0.2s",
          opacity:      voiceConnecting ? 0.6 : 1,
        }}
      >
        <MicIcon color={micIconColor} />
      </button>

      <input
        value={text}
        disabled={textDisabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") send(); }}
        placeholder={voiceActive ? "Voice mode active…" : "Ask anything…"}
        style={{
          flex:         1,
          borderRadius: 9999,
          border:       "1px solid #E8DFD0",
          padding:      "10px 18px",
          fontSize:     14,
          background:   textDisabled ? "#F5F2EC" : "#FFFCF6",
          color:        textDisabled ? "#B0A898" : "#3A342B",
          cursor:       textDisabled ? "not-allowed" : "text",
        }}
      />
      <button
        onClick={send}
        disabled={textDisabled || !text.trim()}
        style={{
          borderRadius: 9999,
          border:       "none",
          padding:      "10px 16px",
          background:   textDisabled ? "#E8DFD0" : "#D85F3B",
          color:        textDisabled ? "#B0A898" : "#fff",
          cursor:       textDisabled ? "not-allowed" : "pointer",
          transition:   "background 0.2s",
        }}
      >
        ↑
      </button>
    </div>
  );
}
