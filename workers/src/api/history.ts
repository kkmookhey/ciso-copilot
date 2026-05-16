import type { Env } from "../index";

export async function getHistory(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const deviceId = url.searchParams.get("device") ?? url.searchParams.get("deviceId");
  if (!deviceId) return new Response("missing device", { status: 400 });

  const rows = await env.DB.prepare(`
    SELECT date, generated_at, items FROM briefs
    WHERE device_id = ? AND date >= date('now', '-14 days')
    ORDER BY date DESC
  `).bind(deviceId).all<{ date: string; generated_at: string; items: string }>();

  return Response.json({
    history: rows.results.map(r => ({
      date:        r.date,
      generatedAt: r.generated_at,
      itemCount:   safeCount(r.items),
    })),
  });
}

function safeCount(items: string): number {
  try { return (JSON.parse(items) as unknown[]).length; }
  catch { return 0; }
}
