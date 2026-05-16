import type { Env } from "../index";

const NVD_API   = "https://services.nvd.nist.gov/rest/json/cves/2.0";
const PAGE_SIZE = 500;          // NVD allows up to 2000; smaller pages are kinder
const D1_BATCH  = 50;
const WINDOW_DAYS = 7;          // pull last 7 days on every run (idempotent via upsert)

interface NvdCve {
  cve: {
    id: string;
    lastModified: string;
    published: string;
    descriptions: Array<{ lang: string; value: string }>;
    metrics?: {
      cvssMetricV31?: Array<{ cvssData: { baseScore: number; vectorString: string } }>;
      cvssMetricV30?: Array<{ cvssData: { baseScore: number; vectorString: string } }>;
      cvssMetricV2?:  Array<{ cvssData: { baseScore: number; vectorString: string } }>;
    };
    configurations?: Array<{
      nodes: Array<{
        cpeMatch: Array<{ criteria: string; vulnerable: boolean }>;
      }>;
    }>;
  };
}

interface NvdResponse {
  resultsPerPage: number;
  startIndex:     number;
  totalResults:   number;
  vulnerabilities: NvdCve[];
}

export async function handleNvdCron(env: Env): Promise<void> {
  const now      = new Date();
  const start    = new Date(now.getTime() - WINDOW_DAYS * 86_400_000);
  const startStr = isoToNvd(start);
  const endStr   = isoToNvd(now);

  console.log(`NVD: syncing ${startStr} → ${endStr}`);

  let startIndex = 0;
  let total      = Infinity;
  let written    = 0;

  while (startIndex < total) {
    const url = new URL(NVD_API);
    url.searchParams.set("lastModStartDate", startStr);
    url.searchParams.set("lastModEndDate",   endStr);
    url.searchParams.set("startIndex",       String(startIndex));
    url.searchParams.set("resultsPerPage",   String(PAGE_SIZE));

    const res = await fetch(url.toString(), {
      headers: {
        "apiKey":     env.NVD_API_KEY,
        "User-Agent": "ciso-copilot/0.1",
      },
    });
    if (!res.ok) throw new Error(`NVD fetch failed: ${res.status} ${res.statusText}`);

    const body = await res.text();
    await env.RAW.put(`nvd/${now.toISOString()}-p${startIndex}.json`, body, {
      httpMetadata: { contentType: "application/json" },
    });

    const data: NvdResponse = JSON.parse(body);
    total = data.totalResults;

    written += await writeBatch(env, data.vulnerabilities);
    console.log(`NVD: page @${startIndex}, total=${total}, written so far=${written}`);

    startIndex += data.resultsPerPage;

    // Politeness: NVD allows 50 req / 30s with a key. One page per second is well under.
    if (startIndex < total) await sleep(1000);
  }

  console.log(`NVD: done. ${written} CVEs upserted.`);
}

async function writeBatch(env: Env, vulns: NvdCve[]): Promise<number> {
  const stmt = env.DB.prepare(`
    INSERT INTO cves (
      cve_id, description, cvss_score, cvss_vector, published_at, last_modified,
      cpe_matches, vendors, products
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(cve_id) DO UPDATE SET
      description   = excluded.description,
      cvss_score    = excluded.cvss_score,
      cvss_vector   = excluded.cvss_vector,
      last_modified = excluded.last_modified,
      cpe_matches   = excluded.cpe_matches,
      vendors       = excluded.vendors,
      products      = excluded.products
  `);

  const statements: D1PreparedStatement[] = [];
  for (const item of vulns) {
    const c = item.cve;
    const desc = c.descriptions?.find(d => d.lang === "en")?.value ?? null;

    const cvss = c.metrics?.cvssMetricV31?.[0]?.cvssData
              ?? c.metrics?.cvssMetricV30?.[0]?.cvssData
              ?? c.metrics?.cvssMetricV2?.[0]?.cvssData
              ?? null;

    const cpes:     string[]   = [];
    const vendors:  Set<string> = new Set();
    const products: Set<string> = new Set();

    if (c.configurations) {
      for (const cfg of c.configurations) {
        for (const node of cfg.nodes) {
          for (const m of node.cpeMatch) {
            cpes.push(m.criteria);
            // CPE 2.3: cpe:2.3:part:vendor:product:version:update:edition:lang:sw_edition:target_sw:target_hw:other
            const parts = m.criteria.split(":");
            if (parts.length >= 5) {
              if (parts[3] && parts[3] !== "*") vendors.add(parts[3].toLowerCase());
              if (parts[4] && parts[4] !== "*") products.add(parts[4].toLowerCase());
            }
          }
        }
      }
    }

    statements.push(stmt.bind(
      c.id,
      desc,
      cvss?.baseScore     ?? null,
      cvss?.vectorString  ?? null,
      c.published,
      c.lastModified,
      JSON.stringify(cpes),
      JSON.stringify([...vendors]),
      JSON.stringify([...products]),
    ));
  }

  for (let i = 0; i < statements.length; i += D1_BATCH) {
    await env.DB.batch(statements.slice(i, i + D1_BATCH));
  }
  return statements.length;
}

function isoToNvd(d: Date): string {
  // NVD expects "YYYY-MM-DDTHH:mm:ss.SSS" (no Z).
  return d.toISOString().replace(/Z$/, "");
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}
