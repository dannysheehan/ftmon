#!/bin/sh
# TS-17: capture soak evidence on a running server-profile host.
set -eu

stamp=$(date -u +%Y%m%d)
outdir=/var/lib/ftmon/soak/evidence
manifest=/var/lib/ftmon/soak/manifest.json
db=/var/lib/ftmon/.local/share/ftmon/ftmon.db
py=/opt/ftmon/tools/ftmon/bin/python
mkdir -p "$outdir"

# The report resolves the deployed self.toml through ftmon.paths to name the
# calibrated cpu_budget_pct RB-01 v0.66 requires evidence to state. This account
# has no login session, so it needs the same explicit locations the unit sets --
# without them the lookup lands on the invoking user's empty default and the
# report correctly refuses to guess which figure the daemon alarms at.
export FTMON_CONFIG_DIR=/var/lib/ftmon/.config/ftmon
export FTMON_DATA_DIR=/var/lib/ftmon/.local/share/ftmon
export FTMON_STATE_DIR=/var/lib/ftmon/.local/state/ftmon
export FTMON_RUNTIME_DIR=/run/ftmon
export FTMON_CHECK_REGISTRY=/etc/ftmon/checks.toml

# A leg upgraded in place keeps the previous build's history in the same
# database, so a rolling 30-day window reports the build that was replaced
# (issue #178). The manifest already records when this leg started; prefer the
# started clock, fall back to the deployment, and accept a plain 30 days only
# when there is no manifest to say otherwise.
since=$($py - "$manifest" <<'PY' 2>/dev/null || true
import json, sys
try:
    manifest = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
print(manifest.get("window_starts_at") or manifest.get("deployed_at") or "", end="")
PY
)

if [ -n "$since" ]; then
  $py /opt/ftmon/src/tools/soak_report.py "$db" \
    --since "$since" -o "$outdir/demo-server-${stamp}.md"
else
  $py /opt/ftmon/src/tools/soak_report.py "$db" \
    -o "$outdir/demo-server-${stamp}.md"
fi

/usr/local/bin/ftmon doctor > "$outdir/demo-server-doctor-${stamp}.txt" 2>&1
/usr/local/bin/ftmon incidents --all > "$outdir/demo-server-incidents-${stamp}.txt" 2>&1 || true
