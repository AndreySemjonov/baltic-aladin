#!/usr/bin/env bash
# One WRF run on the home PC "Tower" (the owner, 18.10.2026: two a day, by Windows' Task Scheduler,
# for a week's test before the Mac mini): the newest code, wrf_poc/local.sh with the faster settings
# tested on the laptop (the adaptive time step; the 1 km box from the riding light ahead, daylight.py),
# then its tiles to GitHub (the release asset wrf-local/<region>-tiles.tgz) and wrf-publish.yml to put
# them on our file store. One run at a time; each run's log in /root/wrf-logs.
#
#   bash /root/baltic-aladin/wrf_poc/tower.sh [region] [hours]      (tarifa 48)
set -uo pipefail
REGION=${1:-tarifa}
HOURS=${2:-48}
HERE=/root/baltic-aladin
REPO=AndreySemjonov/baltic-aladin
mkdir -p /root/wrf-logs
LOG=/root/wrf-logs/$(date -u +%Y%m%dT%H%MZ)-$REGION.log
exec > >(tee -a "$LOG") 2>&1
log() { echo "$(date -u +%H:%M:%S) $*"; }

# The GFS cycle waited for first, before WRF's lock (the owner, 21.10.2026, W1: the tasks start at 11:45 and 23:45
# Riga and the run begins as soon as NOAA has the cycle's hours, about 40 minutes sooner than the old fixed start;
# the runner treats a held lock as WRF running).
export GFS_BACK=2 GFS_WAIT=100 GFS_POLL=120
( cd "$HERE" && HOURS="$HOURS" "$HERE/.venv/bin/python" wrf_poc/gfs.py --wait ) || log "the GFS wait failed: WRF picks its cycle itself"

exec 9> /root/wrf-logs/.lock
if ! flock -n 9; then
  log "a run is going already: this one left out"
  exit 0
fi
service docker start > /dev/null 2>&1 || true
git -C "$HERE" pull -q --rebase --autostash || log "git pull failed: the code as it was"

# The terrain data on drive D: when there is one (tower_setup.sh).
# 8 processes: the fastest on the Tower (18.10.2026, 3 hours at 1 km: 8 740 s, 12 810 s, 20 about 900 s;
# its 8 fast cores, the slower ones and the second threads held the others back).
export WRF_CORES=${WRF_CORES:-8}
export ADAPTIVE=1 DAYLIGHT=${DAYLIGHT:-1} WRF_WORK=/root/wrf-work/$REGION WRF_GEOG=${WRF_GEOG:-$([ -d /mnt/d/meteogust/geog ] && echo /mnt/d/meteogust/geog || echo /root/geog)}
started=$(date +%s)
log "WRF $REGION, $HOURS hours, on $(nproc) cores"
if ! bash "$HERE/wrf_poc/local.sh" "$REGION" "$HOURS"; then
  log "the run failed"
  exit 1
fi
log "the run took $(( ($(date +%s) - started) / 60 )) minutes"
TGZ=/root/wrf-work/$REGION/$REGION-tiles.tgz
if [ -f "$TGZ" ]; then
  gh release upload wrf-local "$TGZ" --clobber -R "$REPO" && gh workflow run wrf-publish.yml -f region="$REGION" -R "$REPO" \
    && log "tiles sent: wrf-publish.yml puts them on our file store" || log "the upload failed"
else
  log "no tiles to send"
fi
# A fortnight of logs.
find /root/wrf-logs -name "*.log" -mtime +14 -delete
