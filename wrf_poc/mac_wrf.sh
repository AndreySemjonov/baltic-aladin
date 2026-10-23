#!/usr/bin/env bash
# One WRF run on the Mac mini (the owner, 23.10.2026: "A: move both WRF runs to the Mac on 6 processes, the Tower's
# WRF as the fallback"; the benchmark: about twice the Tower's speed). local.sh's steps natively: WPS and WRF built by
# mac_build_wps.sh / mac_build.sh in ~/wrf, the terrain data in ~/wrf/geog, the Python steps in this repo's .venv.
# Started by launchd at 11:45 and 23:45 Riga (~/Library/LaunchAgents/com.meteogust.wrf.plist), as tower.sh is on the
# Tower. Its state goes to runner/wrf-<region>.json at each stage and every 5 minutes (wrf_state.py), so tower.sh stands
# aside while it works and runs the cycle itself when it fails. The tiles go straight to our file store
# (publish_tiles.py, as wrf-publish.yml does) with INGEST_TOKEN from ~/.meteogust.env (the owner's).
# NO_UPLOAD=1, or no token: a test, nothing sent (no state either, so the Tower runs as usual).
#
#   bash ~/baltic-aladin/wrf_poc/mac_wrf.sh [region] [hours]      (tarifa 48)
set -uo pipefail
REGION=${1:-tarifa}
HOURS=${2:-48}
HERE=$HOME/baltic-aladin
PY=$HERE/.venv/bin/python
# GNU date for run_wrf.sh (date -d), Homebrew's MPI, compilers and NetCDF.
export PATH=/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/bin:$PATH
mkdir -p "$HOME/wrf-logs"
LOG=$HOME/wrf-logs/$(date -u +%Y%m%dT%H%MZ)-$REGION.log
exec > >(tee -a "$LOG") 2>&1
log() { echo "$(date -u +%H:%M:%S) $*"; }

[ -f "$HOME/.meteogust.env" ] && { set -a; . "$HOME/.meteogust.env"; set +a; }
TEST=${NO_UPLOAD:-0}
if [ "$TEST" != 1 ] && [ -z "${INGEST_TOKEN:-}" ]; then
  log "no INGEST_TOKEN in ~/.meteogust.env: a test, nothing sent"
  TEST=1
fi
CYCLE=""
stage() {
  echo "$1" > "$HOME/wrf-logs/.stage-$REGION"
  log "stage: $1${CYCLE:+ ($CYCLE)}"
  if [ "$TEST" != 1 ]; then "$PY" "$HERE/wrf_poc/wrf_state.py" put "$REGION" "$1" "$CYCLE" || log "state not sent"; fi
}
fail() { log "failed: $1"; stage failed; exit 1; }

# One run at a time (macOS has no flock): a lock folder with the run's process number.
LOCK=$HOME/wrf-logs/.lock-$REGION
if ! mkdir "$LOCK" 2> /dev/null; then
  if kill -0 "$(cat "$LOCK/pid" 2> /dev/null)" 2> /dev/null; then log "a run is going already: this one left out"; exit 0; fi
  log "a stale lock (its run is gone): taken over"
fi
echo $$ > "$LOCK/pid"
BEAT=""
trap 'rm -rf "$LOCK"; [ -n "$BEAT" ] && kill "$BEAT" 2> /dev/null' EXIT

cd "$HERE" || exit 1
git pull -q --rebase --autostash || log "git pull failed: the code as it was"
stage waiting
# Every 5 minutes the stage again (the Tower's sign the Mac is alive; wrf_state.py STALE is 20 minutes).
if [ "$TEST" != 1 ]; then
  ( while sleep 300; do "$PY" "$HERE/wrf_poc/wrf_state.py" put "$REGION" "$(cat "$HOME/wrf-logs/.stage-$REGION")" "$CYCLE" \
      > /dev/null 2>&1; done ) &
  BEAT=$!
fi

# The GFS cycle waited for first, as tower.sh does (W1: the run begins as soon as NOAA has the cycle's hours).
export GFS_BACK=2 GFS_WAIT=100 GFS_POLL=120
HOURS="$HOURS" "$PY" wrf_poc/gfs.py --wait || log "the GFS wait failed: WRF picks its cycle itself"

started=$(date +%s)
WORK=$HOME/wrf-work/$REGION
rm -rf "$WORK" && mkdir -p "$WORK"
set -a
# shellcheck disable=SC1090
source "wrf_poc/regions/$REGION.env"
set +a
export HOURS REGION
log "GFS for $REGION, $HOURS hours"
"$PY" wrf_poc/gfs.py "$WORK" || fail "GFS"
CYCLE=$(sed -n 's/^CYCLE=//p' "$WORK/env")
# The beat reads the cycle from here from now on.
[ -n "$BEAT" ] && { kill "$BEAT" 2> /dev/null; ( while sleep 300; do "$PY" "$HERE/wrf_poc/wrf_state.py" put "$REGION" \
  "$(cat "$HOME/wrf-logs/.stage-$REGION")" "$CYCLE" > /dev/null 2>&1; done ) & BEAT=$!; }

# run_wps.sh and run_wrf.sh with the Mac's folders (their defaults are the container's).
export WRF_WORKDIR=$WORK WRF_POCDIR=$HERE/wrf_poc WRF_GEOGDIR=$HOME/wrf/geog WRF_WPSDIR=$HOME/wrf/WPS \
       WRF_RUNDIR=$HOME/wrf/WRF/run
stage wps
bash wrf_poc/run_wps.sh > "$WORK/wps.log" 2>&1 || { tail -20 "$WORK/wps.log"; fail "WPS"; }
"$PY" wrf_poc/levels.py "$WORK" || fail "levels"
# The faster settings, as on the Tower: the adaptive step, the 1 km box from the riding light ahead (daylight.py).
if [ "${DAYLIGHT:-1}" = 1 ] && [ -z "${D3_FROM:-}" ]; then
  eval "export $("$PY" wrf_poc/daylight.py "$WORK")"
  log "1 km box in daylight: hours $D3_FROM to $D3_TO"
fi
# 6 processes: the benchmark (23.10.2026) found 6 nearly as fast as all 10 cores, 4 left for the owner's Xcode.
export ADAPTIVE=1 WRF_CORES=${WRF_CORES:-6}
stage wrf
bash wrf_poc/run_wrf.sh > "$WORK/wrf.log" 2>&1 || { tail -30 "$WORK/wrf.log"; fail "WRF"; }
log "WPS and WRF took $(( ($(date +%s) - started) / 60 )) minutes ($(tr '\n' ' ' < "$WORK/times.txt"))"

stage tiles
"$PY" wrf_poc/report.py "$WORK" > "$WORK/report.md" || true
rm -rf "$WORK/tiles"
"$PY" wrf_poc/tiles.py "$WORK" --local "$WORK/tiles" || fail "tiles"
# The run's wind at the airports and spots (points.py), uploaded with the tiles.
"$PY" wrf_poc/points.py "$WORK" "$WORK/tiles/points.json" || log "no points"
if [ "$TEST" = 1 ]; then
  log "test: tiles in $WORK/tiles, nothing uploaded"
else
  "$PY" wrf_poc/publish_tiles.py "$WORK/tiles" "wrf-$REGION" || fail "upload"
fi
stage done
log "the run took $(( ($(date +%s) - started) / 60 )) minutes"
# A fortnight of logs.
find "$HOME/wrf-logs" -name "*.log" -mtime +14 -delete
