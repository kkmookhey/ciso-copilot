import type { Env } from "../index";
import { generateBriefForUser } from "../cron/brief";

export async function postProfile(req: Request, env: Env): Promise<Response> {
  const body = await req.json() as {
    deviceId: string;
    stackProfile: unknown;
    deviceToken?: string;
    prefs?: unknown;
  };

  if (!body.deviceId || !body.stackProfile) {
    return new Response("missing deviceId or stackProfile", { status: 400 });
  }

  const now = new Date().toISOString();
  await env.DB.prepare(`
    INSERT INTO users (device_id, stack_profile, device_token, prefs, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(device_id) DO UPDATE SET
      stack_profile = excluded.stack_profile,
      device_token  = COALESCE(excluded.device_token, users.device_token),
      prefs         = excluded.prefs,
      updated_at    = excluded.updated_at
  `).bind(
    body.deviceId,
    JSON.stringify(body.stackProfile),
    body.deviceToken ?? null,
    JSON.stringify(body.prefs ?? {}),
    now,
    now,
  ).run();

  // Generate today's brief immediately so the iOS app navigates straight to populated
  // content. ~15s wall time for 5 items; the iOS submit button is already spinner-bound
  // during this await. The nightly cron still runs and refreshes subsequent days.
  const user = await env.DB.prepare(
    "SELECT device_id, stack_profile, device_token FROM users WHERE device_id = ?",
  ).bind(body.deviceId).first<{ device_id: string; stack_profile: string; device_token: string | null }>();

  if (user) {
    try {
      await generateBriefForUser(env, user);
    } catch (err) {
      console.error(`profile: brief gen failed for ${body.deviceId}:`, err);
      // Don't fail profile creation — nightly cron will pick it up.
    }
  }

  return Response.json({ ok: true });
}
