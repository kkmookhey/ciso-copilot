import type { Env } from "../index";

export async function getBrief(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const deviceId = url.searchParams.get("device") ?? url.searchParams.get("deviceId");
  if (!deviceId) return new Response("missing device", { status: 400 });

  const date = url.searchParams.get("date") ?? new Date().toISOString().split("T")[0];

  const row = await env.DB.prepare(`
    SELECT items, generated_at FROM briefs WHERE device_id = ? AND date = ?
  `).bind(deviceId, date).first<{ items: string; generated_at: string }>();

  if (!row) return Response.json({ date, items: [], generatedAt: null });

  return Response.json({
    date,
    items:       JSON.parse(row.items),
    generatedAt: row.generated_at,
  });
}
