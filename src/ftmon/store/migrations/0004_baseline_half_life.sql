-- A baseline can only be reconstructed exactly when every update in its
-- lifetime used the same EWMA coefficient. Existing rows used the three-day
-- default, while future writes persist the effective value explicitly (CA-05).
ALTER TABLE baselines
  ADD COLUMN half_life_s REAL NOT NULL DEFAULT 259200;
