import type { Env } from "../index";

const EPSS_URL = "https://epss.empiricalsecurity.com/epss_scores-current.csv.gz";
const D1_BATCH = 100;

export async function handleEpssCron(env: Env): Promise<void> {
  const res = await fetch(EPSS_URL, {
    headers: { "User-Agent": "ciso-copilot/0.1" },
  });
  if (!res.ok) throw new Error(`EPSS fetch failed: ${res.status}`);
  if (!res.body) throw new Error("EPSS response had no body");

  // The file is .gz served as application/gzip; gunzip with DecompressionStream.
  const text = await new Response(res.body.pipeThrough(new DecompressionStream("gzip"))).text();

  const now = new Date().toISOString();
  await env.RAW.put(`epss/${now}.csv`, text, { httpMetadata: { contentType: "text/csv" } });

  // EPSS is ~250k rows. Filter to CVEs we already track to keep D1 lean.
  const known = await env.DB.prepare("SELECT cve_id FROM cves").all<{ cve_id: string }>();
  const knownSet = new Set(known.results.map(r => r.cve_id));
  console.log(`EPSS: ${knownSet.size} known CVEs; filtering CSV.`);

  const today = now.split("T")[0];
  const stmt = env.DB.prepare(`
    INSERT INTO epss (cve_id, score, percentile, date)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(cve_id) DO UPDATE SET
      score      = excluded.score,
      percentile = excluded.percentile,
      date       = excluded.date
  `);

  const statements: D1PreparedStatement[] = [];
  for (const line of text.split("\n")) {
    if (!line || line.startsWith("#") || line.startsWith("cve,")) continue;
    const [cveId, scoreStr, percentileStr] = line.split(",");
    if (!cveId || !scoreStr) continue;
    const id = cveId.trim();
    if (!knownSet.has(id)) continue;

    statements.push(stmt.bind(id, parseFloat(scoreStr), parseFloat(percentileStr ?? "0"), today));
  }

  console.log(`EPSS: ${statements.length} rows to write.`);
  for (let i = 0; i < statements.length; i += D1_BATCH) {
    await env.DB.batch(statements.slice(i, i + D1_BATCH));
  }
}
