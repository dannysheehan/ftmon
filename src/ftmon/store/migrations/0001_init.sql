-- FTMON v2 schema v1 (DESIGN.md section 8). Applied by ftmon.store.db.migrate,
-- gated on PRAGMA user_version. Do not edit after release; add 0002_*.sql instead.

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
-- keys: schema_hash, last_tick_ts, last_tick_mono, rollup5m_cursor, rollup1h_cursor, db_budget_state

CREATE TABLE series(
  id INTEGER PRIMARY KEY, monitor TEXT NOT NULL, entity_id TEXT NOT NULL,
  metric TEXT NOT NULL, durable INTEGER NOT NULL,            -- section 9: 1 = system/disk/self/watchlist
  UNIQUE(monitor, entity_id, metric));

CREATE TABLE samples(   series_id INTEGER NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL,
  PRIMARY KEY(series_id, ts)) WITHOUT ROWID;                 -- DM-01; ~35 B/row effective

CREATE TABLE rollup5m(  series_id INTEGER NOT NULL, bucket INTEGER NOT NULL,
  avg REAL, min REAL, max REAL, last REAL, cnt INTEGER,
  PRIMARY KEY(series_id, bucket)) WITHOUT ROWID;             -- DM-04

CREATE TABLE rollup1h(  series_id INTEGER NOT NULL, bucket INTEGER NOT NULL,
  avg REAL, min REAL, max REAL, last REAL, cnt INTEGER,
  PRIMARY KEY(series_id, bucket)) WITHOUT ROWID;             -- DM-04 (same shape as rollup5m)

CREATE TABLE entities(  monitor TEXT, entity_id TEXT, first_seen INT, last_seen INT,
  gone_ts INT, attrs TEXT CHECK(length(attrs) <= 4096),      -- DM-03, CA-08
  PRIMARY KEY(monitor, entity_id)) WITHOUT ROWID;

CREATE TABLE events(    id INTEGER PRIMARY KEY,              -- id = ingest order (DM-15)
  ts INT, ingest_ts INT, source TEXT, provider TEXT, event_id TEXT,
  severity INT, message TEXT, attrs TEXT);
CREATE INDEX events_ts ON events(ts);
CREATE INDEX events_prov ON events(provider, severity);

CREATE TABLE incidents( id INTEGER PRIMARY KEY, monitor TEXT, grp TEXT, entity_id TEXT,
  state TEXT, severity INT, owning_rule TEXT, opened_ts INT, last_change_ts INT,
  cleared_ts INT, clear_reason TEXT, ack_by TEXT, ack_ts INT,
  notify_count INT, occurrences INT, flapping INT DEFAULT 0);        -- DM-11
CREATE UNIQUE INDEX inc_live ON incidents(monitor, grp, entity_id) WHERE state != 'cleared';
CREATE INDEX inc_state ON incidents(state, last_change_ts);

CREATE TABLE incident_history(incident_id INT, seq INT, ts INT, kind TEXT, detail TEXT,
  PRIMARY KEY(incident_id, seq)) WITHOUT ROWID;              -- DM-12/13 (cap enforced in code)

CREATE TABLE outbox(    id INTEGER PRIMARY KEY, incident_id INT, kind TEXT, body TEXT,
  created_ts INT, delivered_ts INT, stale INT DEFAULT 0);    -- DM-14/NO-04
CREATE INDEX outbox_undelivered ON outbox(created_ts) WHERE delivered_ts IS NULL;

CREATE TABLE baselines( series_id INTEGER PRIMARY KEY, value REAL, updates INT,
  updated_bucket INT) WITHOUT ROWID;                         -- CA-05
CREATE TABLE cursors(   source TEXT PRIMARY KEY, cursor TEXT, updated_ts INT) WITHOUT ROWID;
CREATE TABLE monitor_loads(monitor TEXT, loaded_ts INT, hash TEXT, normalized TEXT,
  PRIMARY KEY(monitor, loaded_ts)) WITHOUT ROWID;            -- PM-07 (keep last 20/monitor)
