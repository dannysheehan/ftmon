-- Persistent action rate limit (AC-02). Reservation happens before launch so
-- a daemon crash cannot immediately repeat a potentially destructive action.
CREATE TABLE action_runs(
  action TEXT PRIMARY KEY,
  last_run_ts INT NOT NULL
) WITHOUT ROWID;
