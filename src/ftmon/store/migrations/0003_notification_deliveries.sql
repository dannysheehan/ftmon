-- Split immutable content from channel-specific delivery state. Keeping the
-- rendered message once prevents retry drift, while one row per channel makes
-- partial success durable (DM-14/DM-18/NO-04).
CREATE TABLE notifications(
  id INTEGER PRIMARY KEY, incident_id INT NOT NULL, kind TEXT NOT NULL,
  severity INT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  monitor TEXT NOT NULL, entity_id TEXT NOT NULL, created_ts INT NOT NULL
);

CREATE TABLE notification_deliveries(
  notification_id INT NOT NULL REFERENCES notifications(id),
  channel TEXT NOT NULL, state TEXT NOT NULL,
  attempt_count INT NOT NULL DEFAULT 0, next_attempt_ts INT,
  delivered_ts INT, last_error TEXT CHECK(length(last_error) <= 512),
  PRIMARY KEY(notification_id, channel)
) WITHOUT ROWID;

CREATE INDEX delivery_due ON notification_deliveries(next_attempt_ts)
  WHERE state = 'pending';

INSERT INTO notifications(
  id, incident_id, kind, severity, title, body, monitor, entity_id, created_ts
)
SELECT o.id, o.incident_id, o.kind,
  COALESCE(CAST(json_extract(o.body, '$.severity') AS INTEGER), 0),
  COALESCE(json_extract(o.body, '$.title'), 'ftmon'),
  COALESCE(json_extract(o.body, '$.body'), ''),
  COALESCE(i.monitor, ''), COALESCE(i.entity_id, ''), o.created_ts
FROM outbox AS o LEFT JOIN incidents AS i ON i.id = o.incident_id;

INSERT INTO notification_deliveries(
  notification_id, channel, state, attempt_count, next_attempt_ts,
  delivered_ts, last_error
)
SELECT id, 'file',
  CASE WHEN stale != 0 THEN 'failed'
       WHEN delivered_ts IS NOT NULL THEN 'delivered' ELSE 'pending' END,
  0,
  CASE WHEN stale = 0 AND delivered_ts IS NULL THEN created_ts END,
  CASE WHEN stale = 0 THEN delivered_ts END,
  CASE WHEN stale != 0 THEN 'legacy stale delivery' END
FROM outbox;

DROP TABLE outbox;
