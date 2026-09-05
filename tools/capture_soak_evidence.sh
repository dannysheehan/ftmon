#!/bin/sh
# TS-17: capture soak evidence on a running server-profile host.
set -eu

stamp=$(date -u +%Y%m%d)
outdir=/var/lib/ftmon/soak/evidence
manifest=/var/lib/ftmon/soak/manifest.json
db=/var/lib/ftmon/.local/share/ftmon/ftmon.db
py=/opt/ftmon/tools/ftmon/bin/python
mkdir -p "$outdir"

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
