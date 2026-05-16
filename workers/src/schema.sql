-- CISO Copilot D1 schema. Mirrors CISOBrief.md §8.1, extended for user / brief / cache tables.

-- ===== Vulnerability data (filled by ingestion crons) =====

CREATE TABLE IF NOT EXISTS cves (
  cve_id        TEXT PRIMARY KEY,
  description   TEXT,
  cvss_score    REAL,
  cvss_vector   TEXT,
  published_at  TEXT,
  last_modified TEXT,
  cpe_matches   TEXT,   -- JSON array
  vendors       TEXT,   -- JSON array, lowercase
  products      TEXT    -- JSON array, lowercase
);

CREATE TABLE IF NOT EXISTS kev (
  cve_id          TEXT PRIMARY KEY REFERENCES cves(cve_id),
  date_added      TEXT,
  due_date        TEXT,
  ransomware_use  INTEGER,
  required_action TEXT
);

CREATE TABLE IF NOT EXISTS epss (
  cve_id     TEXT PRIMARY KEY REFERENCES cves(cve_id),
  score      REAL,
  percentile REAL,
  date       TEXT
);

CREATE TABLE IF NOT EXISTS advisories (
  id           TEXT PRIMARY KEY,
  source       TEXT,
  title        TEXT,
  summary      TEXT,
  url          TEXT,
  published_at TEXT,
  vendors      TEXT
);

-- ===== User data =====

CREATE TABLE IF NOT EXISTS users (
  device_id     TEXT PRIMARY KEY,
  stack_profile TEXT,   -- JSON
  device_token  TEXT,   -- APNs
  prefs         TEXT,   -- JSON
  created_at    TEXT,
  updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS briefs (
  device_id    TEXT,
  date         TEXT,
  items        TEXT,   -- JSON array of enriched items
  generated_at TEXT,
  PRIMARY KEY (device_id, date)
);

CREATE TABLE IF NOT EXISTS feedback (
  device_id  TEXT,
  item_id    TEXT,
  sentiment  TEXT,   -- "up" | "down"
  reason     TEXT,
  created_at TEXT,
  PRIMARY KEY (device_id, item_id, created_at)
);

-- ===== LLM response cache (CISOBrief.md §10.4) =====
-- Key: {cve_id}#{prompt_type}            for stack-independent prompts (board_paragraph)
-- Key: {cve_id}#{prompt_type}#{stackHash} for stack-dependent prompts (why_it_matters, team_questions)

CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key            TEXT PRIMARY KEY,
  prompt_type          TEXT,
  response             TEXT,
  model_version        TEXT,
  generated_at         TEXT,
  source_last_modified TEXT
);

-- ===== Indexes =====

CREATE INDEX IF NOT EXISTS idx_cves_modified ON cves(last_modified);
CREATE INDEX IF NOT EXISTS idx_kev_date      ON kev(date_added);
CREATE INDEX IF NOT EXISTS idx_briefs_date   ON briefs(date);
