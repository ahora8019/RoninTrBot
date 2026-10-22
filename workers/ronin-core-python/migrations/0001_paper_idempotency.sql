CREATE TABLE IF NOT EXISTS paper_idempotency (
  idempotency_key TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('in_progress', 'completed')),
  result_json TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_idempotency_state_updated
  ON paper_idempotency(state, updated_at);
