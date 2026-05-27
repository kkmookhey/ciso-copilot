// Cognito OAuth 2.0 authorization-code flow for the web SPA.
// Mirrors iOS's AuthManager but runs in the browser. Tokens live in
// localStorage (yes, XSS-vulnerable — see CISOBrief-v2.md §15 for the
// "harden to httpOnly cookies via backend session" follow-up).

import { env } from "./env";

export const cognito = {
  domain:      "ciso-copilot.auth.us-east-1.amazoncognito.com",
  clientId:    "5vroudnp54n7fdqvjj49ff53br",
  region:      "us-east-1",
  scope:       "openid email profile",

  get redirectUri(): string {
    // Production: https://app.settlingforless.com/callback (when DNS lands)
    // Dev: http://localhost:5173/callback
    return `${window.location.origin}/callback`;
  },

  get authorizeUrl(): string {
    const p = new URLSearchParams({
      client_id:     this.clientId,
      response_type: "code",
      scope:         this.scope,
      redirect_uri:  this.redirectUri,
    });
    return `https://${this.domain}/oauth2/authorize?${p}`;
  },

  get tokenUrl(): string {
    return `https://${this.domain}/oauth2/token`;
  },

  get logoutUrl(): string {
    // Trailing slash is required — Cognito does exact-match against the client's
    // registered LogoutURLs (which CDK registers as `https://.../`).
    const p = new URLSearchParams({
      client_id:  this.clientId,
      logout_uri: `${window.location.origin}/`,
    });
    return `https://${this.domain}/logout?${p}`;
  },
};

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

const K_ID = "ccp_id_token";
const K_AC = "ccp_access_token";
const K_RF = "ccp_refresh_token";
const K_EX = "ccp_id_expires_at";

interface TokenResponse {
  id_token:      string;
  access_token:  string;
  refresh_token?: string;
  expires_in:    number;
  token_type:    string;
}

function persistTokens(t: TokenResponse) {
  localStorage.setItem(K_ID, t.id_token);
  localStorage.setItem(K_AC, t.access_token);
  if (t.refresh_token) localStorage.setItem(K_RF, t.refresh_token);
  localStorage.setItem(K_EX, String(Math.floor(Date.now() / 1000) + t.expires_in));
}

export function isSignedIn(): boolean {
  return !!(localStorage.getItem(K_ID) || localStorage.getItem(K_RF));
}

export async function validIdToken(): Promise<string | null> {
  const tok = localStorage.getItem(K_ID);
  const exp = Number(localStorage.getItem(K_EX) ?? 0);
  if (tok && exp - Math.floor(Date.now() / 1000) > 60) return tok;
  // Try refresh
  const refresh = localStorage.getItem(K_RF);
  if (!refresh) return null;
  try {
    const t = await postTokenExchange({ grant_type: "refresh_token", refresh_token: refresh });
    // Refresh response may not include a new refresh_token; preserve the old.
    if (!t.refresh_token) t.refresh_token = refresh;
    persistTokens(t);
    return t.id_token;
  } catch (e) {
    console.warn("refresh failed", e);
    return null;
  }
}

export function signOut() {
  [K_ID, K_AC, K_RF, K_EX].forEach((k) => localStorage.removeItem(k));
  window.location.href = cognito.logoutUrl;
}

// ---------------------------------------------------------------------------
// OAuth flow
// ---------------------------------------------------------------------------

export function startSignIn() {
  window.location.href = cognito.authorizeUrl;
}

/// Email-first sign-in. Mirrors the iOS AuthManager flow.
/// Calls POST /auth/discover-tenant with the email; backend returns an
/// authorize URL with the right per-tenant Microsoft IdP (or generic Google)
/// hint baked in. Redirect to it.
export async function discoverTenantAndSignIn(email: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/auth/discover-tenant`, {
    method:  "POST",
    headers: { "content-type": "application/json" },
    body:    JSON.stringify({ email, platform: "web" }),
  });
  if (!res.ok) {
    const body = await res.text();
    let msg = `Could not route sign-in (${res.status})`;
    try {
      const j = JSON.parse(body);
      if (j.message) msg = j.message;
      else if (j.error === "tenant_not_found") msg = "We couldn't find a Microsoft 365 or Google Workspace tenant for that email domain.";
      else if (j.error === "invalid_email") msg = "That doesn't look like a valid email.";
      else if (j.error) msg = j.error;
    } catch { /* keep generic msg */ }
    throw new Error(msg);
  }
  const j: { authorize_url: string } = await res.json();
  window.location.href = j.authorize_url;
}

const API_BASE_URL = env.apiBaseUrl;

/** Called by the /callback route — exchanges code for tokens, persists, returns. */
export async function handleCallback(code: string): Promise<void> {
  const t = await postTokenExchange({
    grant_type:    "authorization_code",
    code,
    redirect_uri:  cognito.redirectUri,
  });
  persistTokens(t);
}

async function postTokenExchange(form: Record<string, string>): Promise<TokenResponse> {
  const body = new URLSearchParams({ client_id: cognito.clientId, ...form });
  const res = await fetch(cognito.tokenUrl, {
    method:  "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) throw new Error(`token endpoint ${res.status}: ${await res.text()}`);
  return res.json();
}
