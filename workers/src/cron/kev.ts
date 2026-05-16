import type { Env } from "../index";

const KEV_URL =
  "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json";

interface KevVuln {
  cveID: string;
  vendorProject: string;
  product: string;
  vulnerabilityName: string;
  dateAdded: string;
  shortDescription: string;
  requiredAction: string;
  dueDate: string;
  knownRansomwareCampaignUse: "Known" | "Unknown" | string;
}

interface KevFeed {
  title: string;
  catalogVersion: string;
  dateReleased: string;
  count: number;
  vulnerabilities: KevVuln[];
}

const D1_BATCH_SIZE = 50;

export async function handleKevCron(env: Env): Promise<void> {
  const res = await fetch(KEV_URL, {
    headers: { "User-Agent": "ciso-copilot/0.1 (+https://github.com/kkmookhey/ciso-copilot)" },
  });
  if (!res.ok) throw new Error(`KEV fetch failed: ${res.status} ${res.statusText}`);

  const body = await res.text();
  const now = new Date().toISOString();

  await env.RAW.put(`kev/${now}.json`, body, {
    httpMetadata: { contentType: "application/json" },
  });

  const feed = JSON.parse(body) as KevFeed;
  console.log(`KEV: catalog ${feed.catalogVersion}, ${feed.count} vulnerabilities`);

  const cveStmt = env.DB.prepare(`
    INSERT INTO cves (cve_id, description, last_modified, vendors, products)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(cve_id) DO UPDATE SET
      description   = COALESCE(excluded.description, cves.description),
      last_modified = excluded.last_modified,
      vendors       = excluded.vendors,
      products      = excluded.products
  `);

  const kevStmt = env.DB.prepare(`
    INSERT INTO kev (cve_id, date_added, due_date, ransomware_use, required_action)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(cve_id) DO UPDATE SET
      date_added      = excluded.date_added,
      due_date        = excluded.due_date,
      ransomware_use  = excluded.ransomware_use,
      required_action = excluded.required_action
  `);

  const statements: D1PreparedStatement[] = [];
  for (const v of feed.vulnerabilities) {
    // Normalize to CPE-style: lowercase + spaces → underscores so KEV values line up with
    // both NVD CPE strings and the chip aliases in lib/stack.ts.
    const vendor  = v.vendorProject.toLowerCase().trim().replace(/\s+/g, "_");
    const product = v.product.toLowerCase().trim().replace(/\s+/g, "_");
    statements.push(
      cveStmt.bind(
        v.cveID,
        v.vulnerabilityName,
        v.dateAdded,
        JSON.stringify([vendor]),
        JSON.stringify([product]),
      ),
    );
    statements.push(
      kevStmt.bind(
        v.cveID,
        v.dateAdded,
        v.dueDate,
        v.knownRansomwareCampaignUse === "Known" ? 1 : 0,
        v.requiredAction,
      ),
    );
  }

  for (let i = 0; i < statements.length; i += D1_BATCH_SIZE) {
    await env.DB.batch(statements.slice(i, i + D1_BATCH_SIZE));
  }

  console.log(`KEV: wrote ${feed.vulnerabilities.length} entries to D1`);
}
