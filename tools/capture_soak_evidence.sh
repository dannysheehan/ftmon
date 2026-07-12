#!/bin/sh
# TS-17: capture soak evidence on a running server-profile host.
set -eu

stamp=$(date -u +%Y%m%d)
outdir=/var/lib/ftmon/soak/evidence
mkdir -p "$outdir"

/opt/ftmon/tools/ftmon/bin/python /opt/ftmon/src/tools/soak_report.py \
  /var/lib/ftmon/.local/share/ftmon/ftmon.db \
  -o "$outdir/demo-server-${stamp}.md"

/usr/local/bin/ftmon doctor > "$outdir/demo-server-doctor-${stamp}.txt" 2>&1
/usr/local/bin/ftmon incidents --all > "$outdir/demo-server-incidents-${stamp}.txt" 2>&1 || true
