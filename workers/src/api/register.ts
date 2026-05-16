import type { Env } from "../index";

export async function postRegisterToken(req: Request, env: Env): Promise<Response> {
  const body = await req.json() as { deviceId: string; deviceToken: string };
  if (!body.deviceId || !body.deviceToken) {
    return new Response("missing deviceId or deviceToken", { status: 400 });
  }

  await env.DB.prepare(`
    UPDATE users SET device_token = ?, updated_at = ? WHERE device_id = ?
  `).bind(body.deviceToken, new Date().toISOString(), body.deviceId).run();

  return Response.json({ ok: true });
}
