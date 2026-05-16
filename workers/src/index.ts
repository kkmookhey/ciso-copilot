// CISO Copilot — single Worker entry. fetch() serves the API + debug routes;
// scheduled() dispatches the four crons (KEV / NVD / EPSS / nightly brief).

import { handleKevCron }   from "./cron/kev";
import { handleNvdCron }   from "./cron/nvd";
import { handleEpssCron }  from "./cron/epss";
import { handleBriefCron, generateBriefForUser } from "./cron/brief";

import { postProfile }        from "./api/profile";
import { getBrief }           from "./api/brief";
import { postFeedback }       from "./api/feedback";
import { postRegisterToken }  from "./api/register";
import { getHistory }         from "./api/history";

import { sendApnsPush }       from "./lib/apns";

export interface Env {
  DB: D1Database;
  RAW: R2Bucket;

  // Secrets (set via `wrangler secret put`)
  NVD_API_KEY:       string;
  ANTHROPIC_API_KEY: string;
  APNS_KEY_P8:       string;
  APNS_KEY_ID:       string;
  APNS_TEAM_ID:      string;
  APNS_BUNDLE_ID:    string;
  DEBUG_TOKEN?:      string;
}

const CRON = {
  KEV:   "0 * * * *",    // hourly
  NVD:   "0 */2 * * *",  // every 2h
  EPSS:  "0 6 * * *",    // daily 06:00 UTC
  BRIEF: "30 6 * * *",   // daily 06:30 UTC (after EPSS)
} as const;

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);

    // === API (iOS app talks only to these) ===
    if (req.method === "POST" && url.pathname === "/profile")          return postProfile(req, env);
    if (req.method === "GET"  && url.pathname === "/brief")            return getBrief(req, env);
    if (req.method === "POST" && url.pathname === "/feedback")         return postFeedback(req, env);
    if (req.method === "POST" && url.pathname === "/register-token")   return postRegisterToken(req, env);
    if (req.method === "GET"  && url.pathname === "/history")          return getHistory(req, env);

    // === Debug routes (gated on DEBUG_TOKEN once set) ===
    if (url.pathname.startsWith("/__debug/")) {
      if (env.DEBUG_TOKEN && req.headers.get("x-debug-token") !== env.DEBUG_TOKEN) {
        return new Response("forbidden", { status: 403 });
      }

      if (url.pathname === "/__debug/run/kev")   { ctx.waitUntil(handleKevCron(env));   return new Response("kev triggered\n",   { status: 202 }); }
      if (url.pathname === "/__debug/run/nvd")   { ctx.waitUntil(handleNvdCron(env));   return new Response("nvd triggered\n",   { status: 202 }); }
      if (url.pathname === "/__debug/run/epss")  { ctx.waitUntil(handleEpssCron(env));  return new Response("epss triggered\n",  { status: 202 }); }
      if (url.pathname === "/__debug/run/brief") { ctx.waitUntil(handleBriefCron(env)); return new Response("brief triggered\n", { status: 202 }); }

      if (url.pathname === "/__debug/run/brief-for") {
        const deviceId = url.searchParams.get("device");
        if (!deviceId) return new Response("missing device", { status: 400 });
        const user = await env.DB.prepare(
          "SELECT device_id, stack_profile, device_token FROM users WHERE device_id = ?",
        ).bind(deviceId).first<{ device_id: string; stack_profile: string; device_token: string | null }>();
        if (!user) return new Response("user not found", { status: 404 });
        // Synchronous: returns when the brief is fully generated. Lets us surface LLM errors via curl.
        try {
          await generateBriefForUser(env, user);
          return new Response("brief generated\n", { status: 200 });
        } catch (err) {
          return new Response(`brief failed: ${(err as Error).message}\n`, { status: 500 });
        }
      }

      if (url.pathname === "/__debug/run/push-test") {
        const deviceId = url.searchParams.get("device");
        if (!deviceId) return new Response("missing device", { status: 400 });
        const user = await env.DB.prepare(
          "SELECT device_token FROM users WHERE device_id = ?",
        ).bind(deviceId).first<{ device_token: string | null }>();
        if (!user?.device_token) return new Response("user has no device_token", { status: 404 });
        try {
          await sendApnsPush(env, user.device_token, 1, "CVE-DEMO-TEST");
          return new Response("push sent\n", { status: 200 });
        } catch (err) {
          return new Response(`push failed: ${(err as Error).message}\n`, { status: 500 });
        }
      }

      if (url.pathname === "/__debug/counts") {
        const [cves, kev, epss, users, briefs] = await Promise.all([
          env.DB.prepare("SELECT COUNT(*) AS n FROM cves").first<{ n: number }>(),
          env.DB.prepare("SELECT COUNT(*) AS n FROM kev").first<{ n: number }>(),
          env.DB.prepare("SELECT COUNT(*) AS n FROM epss").first<{ n: number }>(),
          env.DB.prepare("SELECT COUNT(*) AS n FROM users").first<{ n: number }>(),
          env.DB.prepare("SELECT COUNT(*) AS n FROM briefs").first<{ n: number }>(),
        ]);
        return Response.json({
          cves:   cves?.n   ?? 0,
          kev:    kev?.n    ?? 0,
          epss:   epss?.n   ?? 0,
          users:  users?.n  ?? 0,
          briefs: briefs?.n ?? 0,
        });
      }
    }

    return new Response("ciso-copilot worker\n", { status: 200 });
  },

  async scheduled(event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    switch (event.cron) {
      case CRON.KEV:   ctx.waitUntil(handleKevCron(env));   return;
      case CRON.NVD:   ctx.waitUntil(handleNvdCron(env));   return;
      case CRON.EPSS:  ctx.waitUntil(handleEpssCron(env));  return;
      case CRON.BRIEF: ctx.waitUntil(handleBriefCron(env)); return;
      default: console.warn(`unhandled cron: ${event.cron}`);
    }
  },
};
