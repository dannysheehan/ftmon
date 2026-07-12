#!/bin/sh
# Run a repeatable leak/hog tuning session against a live FTMON daemon.
# See docs/tuning-procedure.md for scoring and parameter grids.
set -eu

usage() {
  cat <<'EOF'
usage: stress_profile.sh [-o OUTDIR] [phase ...]

Phases (default: all):
  leak-slow     2 MiB/min for 45m  (firefox-leak scenario rate)
  leak-fast     8 MiB/min for 30m  (should trip stock thresholds)
  leak-burst    200 MiB ramp in 5m then hold 25m
  cpu-sustained stress-ng --cpu 2 for 20m (requires stress-ng)
  cpu-burst     90%% CPU for 30s every 5m for 25m (requires stress-ng)
  cooldown      15m idle capture

Environment:
  FTMON         ftmon binary (default: ftmon)
  LEAKY         leaky.py path (default: beside this script)
  STRESS_CPU    stress-ng workers for cpu-sustained (default: 2)
  QUICK=1         shorten all phases for smoke/dry-run (~15 min total)
EOF
}

OUTDIR=""
PHASES=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) OUTDIR=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) PHASES="$PHASES $1"; shift ;;
  esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
FTMON=${FTMON:-ftmon}
LEAKY=${LEAKY:-$SCRIPT_DIR/leaky.py}
STRESS_CPU=${STRESS_CPU:-2}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUTDIR=${OUTDIR:-./tuning/evidence/$STAMP}
mkdir -p "$OUTDIR"

if [ -z "$(echo "$PHASES" | tr -d ' ')" ]; then
  PHASES="leak-slow leak-fast leak-burst cpu-sustained cpu-burst cooldown"
fi

if [ "${QUICK:-0}" = "1" ]; then
  LEAK_SLOW_SEC=300
  LEAK_FAST_SEC=300
  LEAK_BURST_SEC=300
  LEAK_BURST_MIB=100
  CPU_SUSTAINED_SEC=120
  CPU_BURST_SEC=300
  COOLDOWN_SEC=60
  echo "QUICK=1: shortened phase durations" | tee "$OUTDIR/timeline.txt"
else
  LEAK_SLOW_SEC=2700
  LEAK_FAST_SEC=1800
  LEAK_BURST_SEC=1500
  LEAK_BURST_MIB=200
  CPU_SUSTAINED_SEC=1200
  CPU_BURST_SEC=1500
  COOLDOWN_SEC=900
  : > "$OUTDIR/timeline.txt"
fi

have_stress_ng() {
  command -v stress-ng >/dev/null 2>&1
}

snapshot() {
  label=$1
  safe=$(echo "$label" | tr ' /:' '___')
  "$FTMON" doctor > "$OUTDIR/${safe}-doctor.txt" 2>&1 || true
  "$FTMON" incidents --all > "$OUTDIR/${safe}-incidents.txt" 2>&1 || true
  if [ -f "${HOME}/.local/state/ftmon/notifications.jsonl" ]; then
    cp "${HOME}/.local/state/ftmon/notifications.jsonl" \
      "$OUTDIR/${safe}-notifications.jsonl" || true
  fi
  echo "$label" >> "$OUTDIR/timeline.txt"
  date -u +"%Y-%m-%dT%H:%M:%SZ $label" >> "$OUTDIR/timeline.txt"
}

run_leaky() {
  rate=$1
  duration=$2
  burst=${3:-0}
  name=$4
  extra=""
  if [ "$burst" != "0" ]; then
    extra="--burst-mib $burst"
  fi
  snapshot "before-$name"
  echo "starting $name: ${rate} MiB/h for ${duration}s" | tee -a "$OUTDIR/timeline.txt"
  (cd "$REPO_ROOT" && uv run python "$LEAKY" --process-name "$name" \
    --rate-mib-per-hour "$rate" --duration "$duration" $extra) &
  pid=$!
  echo "$pid" > "$OUTDIR/${name}.pid"
  wait "$pid" || true
  snapshot "after-$name"
}

snapshot "baseline"

for phase in $PHASES; do
  case "$phase" in
    leak-slow)
      run_leaky 120 "$LEAK_SLOW_SEC" 0 tuning-leak-slow
      ;;
    leak-fast)
      run_leaky 480 "$LEAK_FAST_SEC" 0 tuning-leak-fast
      ;;
    leak-burst)
      run_leaky 0 "$LEAK_BURST_SEC" "$LEAK_BURST_MIB" tuning-leak-burst
      ;;
    cpu-sustained)
      if ! have_stress_ng; then
        echo "skip cpu-sustained: stress-ng not installed" | tee -a "$OUTDIR/timeline.txt"
        continue
      fi
      snapshot "before-cpu-sustained"
      echo "cpu-sustained: ${STRESS_CPU} workers for ${CPU_SUSTAINED_SEC}s" | tee -a "$OUTDIR/timeline.txt"
      stress-ng --cpu "$STRESS_CPU" --timeout "${CPU_SUSTAINED_SEC}s" --metrics-brief &
      wait $! || true
      snapshot "after-cpu-sustained"
      ;;
    cpu-burst)
      if ! have_stress_ng; then
        echo "skip cpu-burst: stress-ng not installed" | tee -a "$OUTDIR/timeline.txt"
        continue
      fi
      snapshot "before-cpu-burst"
      end=$(($(date +%s) + CPU_BURST_SEC))
      while [ "$(date +%s)" -lt "$end" ]; do
        stress-ng --cpu "$STRESS_CPU" --cpu-load 90 --timeout 30s --metrics-brief || true
        sleep 270
      done
      snapshot "after-cpu-burst"
      ;;
    cooldown)
      snapshot "before-cooldown"
      echo "cooldown ${COOLDOWN_SEC}s" | tee -a "$OUTDIR/timeline.txt"
      sleep "$COOLDOWN_SEC"
      snapshot "after-cooldown"
      ;;
    *)
      echo "unknown phase: $phase" >&2
      exit 2
      ;;
  esac
done

snapshot "complete"
echo "evidence in $OUTDIR"
