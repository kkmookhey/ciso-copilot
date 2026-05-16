// APNs push from a Cloudflare Worker. ES256 JWT signed in-Worker with the .p8 key
// stored in Workers Secrets (env.APNS_KEY_P8).

import type { Env } from "../index";

// Dev builds installed via Xcode use the sandbox; TestFlight / App Store builds use production.
// Match this to the `aps-environment` value in CISOCopilot.entitlements.
const APNS_HOST = "api.sandbox.push.apple.com";

interface ApnsAlert {
  title: string;
  body: string;
}

export async function sendApnsPush(
  env: Env,
  deviceToken: string,
  actNowCount: number,
  leadCveId: string,
): Promise<void> {
  const jwt = await mintApnsJwt(env);

  const payload = {
    aps: {
      alert: <ApnsAlert>{
        title: actNowCount === 1 ? "Act Now" : `Act Now (${actNowCount} items)`,
        body:  `${leadCveId} — exploited and matches your stack`,
      },
      sound: "default",
      badge: actNowCount,
    },
    cveId: leadCveId,
  };

  const res = await fetch(`https://${APNS_HOST}/3/device/${deviceToken}`, {
    method: "POST",
    headers: {
      "authorization":    `bearer ${jwt}`,
      "apns-topic":       env.APNS_BUNDLE_ID,
      "apns-push-type":   "alert",
      "apns-priority":    "10",
      "content-type":     "application/json",
    },
    body: JSON.stringify(payload),
  });

  const apnsId = res.headers.get("apns-id");
  const body = await res.text();
  console.log(`APNs ${res.status} apns-id=${apnsId} body=${body || "(empty)"}`);
  if (!res.ok) {
    throw new Error(`APNs ${res.status}: ${body || "(empty)"}`);
  }
}

async function mintApnsJwt(env: Env): Promise<string> {
  const header  = { alg: "ES256", kid: env.APNS_KEY_ID, typ: "JWT" };
  const payload = { iss: env.APNS_TEAM_ID, iat: Math.floor(Date.now() / 1000) };

  const headerB64  = base64UrlEncode(new TextEncoder().encode(JSON.stringify(header)));
  const payloadB64 = base64UrlEncode(new TextEncoder().encode(JSON.stringify(payload)));
  const signingInput = `${headerB64}.${payloadB64}`;

  const key = await importP8(env.APNS_KEY_P8);
  const sig = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    key,
    new TextEncoder().encode(signingInput),
  );

  return `${signingInput}.${base64UrlEncode(new Uint8Array(sig))}`;
}

async function importP8(pem: string): Promise<CryptoKey> {
  const cleaned = pem
    .replace(/-----BEGIN PRIVATE KEY-----/g, "")
    .replace(/-----END PRIVATE KEY-----/g, "")
    .replace(/\s+/g, "");
  const der = Uint8Array.from(atob(cleaned), c => c.charCodeAt(0));
  return crypto.subtle.importKey(
    "pkcs8",
    der,
    { name: "ECDSA", namedCurve: "P-256" },
    false,
    ["sign"],
  );
}

function base64UrlEncode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
