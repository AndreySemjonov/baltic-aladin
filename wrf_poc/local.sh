#!/usr/bin/env bash
# The WRF test on a computer of our own (the owner, 17.10.2026: the laptop for now, the Mac mini
# later): the same steps as wrf-poc.yml (NCAR's container, the same scripts) on every core here.
# The newest GFS round the region, WPS, WRF, the report (work/report.md and pictures) and, with
# INGEST_TOKEN in the environment, the 1 km box's map tiles uploaded (the app's "WRF 1 km" map).
# The terrain data (about 30 GB) and the container are fetched the first time.
#
#   wrf_poc/local.sh tarifa 48       region (wrf_poc/regions/<name>.env), forecast hours (3s)
#
# WRF_WORK (default ~/wrf-work/<region>) and WRF_GEOG (default ~/geog) move the folders.
set -euo pipefail
REGION=${1:-tarifa}
HOURS=${2:-24}
HERE=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WRF_WORK:-$HOME/wrf-work/$REGION}
GEOG=${WRF_GEOG:-$HOME/geog}
IMAGE=ncar/iwrf:latest
log() { echo "$(date -u +%H:%M:%S) $*"; }

[ -f "$HERE/wrf_poc/regions/$REGION.env" ] || { echo "No region $REGION"; exit 1; }
if [ ! -d "$GEOG/WPS_GEOG" ]; then
  log "terrain data (once, about 30 GB)"
  mkdir -p "$GEOG"
  curl -sSL https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_high_res_mandatory.tar.gz | tar -xz -C "$GEOG"
fi
docker image inspect "$IMAGE" > /dev/null 2>&1 || { log "the WRF container (once)"; docker pull -q "$IMAGE"; }
if [ ! -x "$HERE/.venv/bin/python" ]; then
  python3 -m venv "$HERE/.venv"
  "$HERE/.venv/bin/pip" install --quiet netCDF4 numpy pillow scipy eccodes
fi

# A clean work folder (the container writes as root: removed through it).
[ -d "$WORK" ] && docker run --rm --entrypoint bash -u root -v "$(dirname "$WORK"):/w" "$IMAGE" -c "rm -rf /w/$(basename "$WORK")"
mkdir -p "$WORK"
cd "$HERE"
set -a
# shellcheck disable=SC1090
source "wrf_poc/regions/$REGION.env"
set +a
export HOURS REGION
log "GFS for $REGION, $HOURS hours"
.venv/bin/python wrf_poc/gfs.py "$WORK"
log "WPS"
docker run --rm --entrypoint bash -u root -v "$WORK:/work" -v "$GEOG:/geog" -v "$HERE/wrf_poc:/poc" "$IMAGE" /poc/run_wps.sh
docker run --rm --entrypoint bash -u root -v "$WORK:/work" "$IMAGE" -c "chmod -R a+rwX /work"
.venv/bin/python wrf_poc/levels.py "$WORK"
log "WRF on $(nproc) cores"
# The faster settings, when given: ADAPTIVE=1, D3_FROM / D3_TO (the 1 km box's hours).
docker run --rm --entrypoint bash -u root --ulimit stack=-1:-1 --shm-size=8g -e ADAPTIVE -e D3_FROM -e D3_TO \
  -v "$WORK:/work" -v "$HERE/wrf_poc:/poc" "$IMAGE" /poc/run_wrf.sh
docker run --rm --entrypoint bash -u root -v "$WORK:/work" "$IMAGE" -c "chmod -R a+rwX /work"
.venv/bin/python wrf_poc/report.py "$WORK" > "$WORK/report.md" || true
if [ "${NO_TILES:-0}" = "1" ]; then
  log "no map tiles (NO_TILES)"
elif [ -n "${INGEST_TOKEN:-}" ]; then
  log "map tiles"
  .venv/bin/python wrf_poc/tiles.py "$WORK"
else
  # No upload key here: the tiles packed for GitHub (wrf-publish.yml uploads them with its secret,
  # from the release asset wrf-local/<region>-tiles.tgz).
  log "map tiles (packed: $WORK/$REGION-tiles.tgz)"
  rm -rf "$WORK/tiles"
  .venv/bin/python wrf_poc/tiles.py "$WORK" --local "$WORK/tiles"
  # The run's wind at the airports and spots (points.py), uploaded with the tiles.
  .venv/bin/python wrf_poc/points.py "$WORK" "$WORK/tiles/points.json" || true
  tar -czf "$WORK/$REGION-tiles.tgz" -C "$WORK/tiles" .
fi
log "done: $WORK/report.md"
