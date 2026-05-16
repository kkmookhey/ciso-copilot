// Matching + ranking (CISOBrief.md §7.5).
// Deterministic. CPE + keyword. No ML, no embeddings.

import type { StackProfile } from "./stack";
import { tokenizeStack } from "./stack";

export type Confidence = "high" | "medium" | "low";
export type Severity = "act_now" | "check_today" | "watch" | "fyi";

export interface MatchedItem {
  cveId: string;
  description: string;
  cvssScore: number;
  epssScore: number;
  inKev: boolean;
  kevDateAdded: string | null;
  matchedVendors: string[];
  matchedProducts: string[];
  confidence: Confidence;
  severity: Severity;
  relevance: number;
  lastModified: string;
}

interface JoinedRow {
  cve_id: string;
  description: string | null;
  cvss_score: number | null;
  vendors: string | null;
  products: string | null;
  last_modified: string | null;
  kev_cve_id: string | null;
  kev_date_added: string | null;
  epss_score: number | null;
}

const CONF_WEIGHT: Record<Confidence, number> = { high: 1.0, medium: 0.6, low: 0.3 };

export async function matchCvesForStack(
  db: D1Database,
  profile: StackProfile,
  maxItems = 50,
): Promise<MatchedItem[]> {
  const { vendors, products } = tokenizeStack(profile);
  if (vendors.size === 0 && products.size === 0) return [];

  // Pull candidate CVEs: anything in KEV, or modified in the last 90 days.
  // 90 days keeps the working set small while still surfacing recent activity.
  const result = await db.prepare(`
    SELECT
      c.cve_id, c.description, c.cvss_score, c.vendors, c.products, c.last_modified,
      k.cve_id      AS kev_cve_id,
      k.date_added  AS kev_date_added,
      e.score       AS epss_score
    FROM cves c
    LEFT JOIN kev  k ON c.cve_id = k.cve_id
    LEFT JOIN epss e ON c.cve_id = e.cve_id
    WHERE c.last_modified > date('now', '-90 days')
       OR k.cve_id IS NOT NULL
    ORDER BY c.last_modified DESC
  `).all<JoinedRow>();

  const items: MatchedItem[] = [];

  for (const row of result.results) {
    const cveVendors  = safeParseStringArray(row.vendors);
    const cveProducts = safeParseStringArray(row.products);

    const matchedV = cveVendors.filter(v => vendors.has(v));
    const matchedP = cveProducts.filter(p => products.has(p));

    if (matchedV.length === 0 && matchedP.length === 0) continue;

    const confidence: Confidence =
      matchedV.length > 0 && matchedP.length > 0 ? "high" :
      matchedV.length > 0                        ? "medium" :
                                                   "low";

    const cvss  = row.cvss_score ?? 0;
    const epss  = row.epss_score ?? 0;
    const inKev = row.kev_cve_id !== null;

    const relevance =
      CONF_WEIGHT[confidence] *
      (0.4 * (cvss / 10) + 0.3 * epss + 0.3 * (inKev ? 1 : 0));

    items.push({
      cveId:           row.cve_id,
      description:     row.description ?? "",
      cvssScore:       cvss,
      epssScore:       epss,
      inKev,
      kevDateAdded:    row.kev_date_added,
      matchedVendors:  matchedV,
      matchedProducts: matchedP,
      confidence,
      severity:        classify(inKev, confidence, epss, cvss),
      relevance,
      lastModified:    row.last_modified ?? "",
    });
  }

  items.sort((a, b) => b.relevance - a.relevance);
  return items.slice(0, maxItems);
}

// CISOBrief.md §5.1 — severity classification.
function classify(inKev: boolean, confidence: Confidence, epss: number, cvss: number): Severity {
  if (inKev && confidence === "high")                                  return "act_now";
  if ((epss >= 0.7 && confidence === "high") ||
      (inKev && confidence === "medium"))                              return "check_today";
  if (cvss >= 7.0)                                                     return "watch";
  return "fyi";
}

function safeParseStringArray(s: string | null): string[] {
  if (!s) return [];
  try {
    const parsed = JSON.parse(s);
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}
