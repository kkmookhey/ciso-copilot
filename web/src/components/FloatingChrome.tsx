// FloatingChrome — viewport-fixed funnel chrome: Refer a Friend (top-right)
// + Report a Bug (bottom-right). Rendered once per authed shell so every
// page picks it up without per-route plumbing.

const REFER_SUBJECT = "Get your free cloud and AI scan done";
const BUG_TO = "hello@transilience.ai";
const BUG_SUBJECT = "Bug report — Shasta";

function buildReferHref(): string {
  const signupUrl = `${window.location.origin}/signin`;
  const body =
    `Hey,\n\n` +
    `I've been using Shasta by Transilience — it runs cloud security and ` +
    `AI security scans across AWS, Azure, GCP, and Entra in one place, ` +
    `then maps everything to the frameworks you actually care about ` +
    `(SOC 2, ISO 27001, NIST AI RMF, EU AI Act). Thought you'd find it ` +
    `useful too.\n\n` +
    `Sign up here — first scan is free:\n${signupUrl}\n\n` +
    `— Sent from Shasta`;
  return `mailto:?subject=${encodeURIComponent(REFER_SUBJECT)}` +
         `&body=${encodeURIComponent(body)}`;
}

function buildBugHref(): string {
  const body =
    `What happened:\n\n\n` +
    `What you expected:\n\n\n` +
    `URL when it happened: ${window.location.href}\n` +
    `Browser: ${navigator.userAgent}\n`;
  return `mailto:${BUG_TO}?subject=${encodeURIComponent(BUG_SUBJECT)}` +
         `&body=${encodeURIComponent(body)}`;
}

export function FloatingChrome() {
  return (
    <>
      <a
        href={buildReferHref()}
        title="Email a friend an invite to try Shasta"
        style={{
          position: "fixed", top: 16, right: 20, zIndex: 50,
          background: "#3A342B", color: "#FFFCF6",
          padding: "8px 14px", borderRadius: 999,
          fontSize: 13, fontWeight: 600, textDecoration: "none",
          boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
          border: "1px solid #4A4238",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLAnchorElement).style.background = "#4A4238";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLAnchorElement).style.background = "#3A342B";
        }}
      >
        Refer a friend
      </a>

      <a
        href={buildBugHref()}
        title={`Email ${BUG_TO}`}
        style={{
          position: "fixed", bottom: 12, right: 20, zIndex: 50,
          color: "#7A7268", fontSize: 12, textDecoration: "none",
          padding: "4px 8px",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLAnchorElement).style.color = "#3A342B";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLAnchorElement).style.color = "#7A7268";
        }}
      >
        Report a bug
      </a>
    </>
  );
}
