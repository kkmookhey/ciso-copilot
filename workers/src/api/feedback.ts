import type { Env } from "../index";

export async function postFeedback(req: Request, env: Env): Promise<Response> {
  const body = await req.json() as {
    deviceId: string;
    itemId: string;
    sentiment: "up" | "down";
    reason?: string;
  };

  if (!body.deviceId || !body.itemId || (body.sentiment !== "up" && body.sentiment !== "down")) {
    return new Response("missing or invalid fields", { status: 400 });
  }

  await env.DB.prepare(`
    INSERT INTO feedback (device_id, item_id, sentiment, reason, created_at)
    VALUES (?, ?, ?, ?, ?)
  `).bind(
    body.deviceId,
    body.itemId,
    body.sentiment,
    body.reason ?? null,
    new Date().toISOString(),
  ).run();

  return Response.json({ ok: true });
}
