#!/bin/bash
# The WRF test's preprocessing inside NCAR's i-WRF container (wrf-poc.yml): geogrid (the terrain of
# three nested boxes round Tarifa: 9, 3 and 1 km), ungrib (the GFS files), metgrid (GFS onto the
# boxes). Each step timed into /work/times.txt. START and END come from /work/env.
set -euo pipefail
source /work/env
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
timed() { local name=$1; shift; local t0; t0=$(date +%s); "$@" || return $?; echo "$name $(( $(date +%s) - t0 ))" >> /work/times.txt; log "$name done"; }
# The container's own set-up (its entrypoint is bypassed): Intel's MPI and compilers, the user's paths.
set +u
[ -f /opt/intel/oneapi/setvars.sh ] && source /opt/intel/oneapi/setvars.sh --force > /dev/null 2>&1 || true
[ -f /home/wrfuser/.bashrc ] && source /home/wrfuser/.bashrc > /dev/null 2>&1 || true
set -u

GEOGRID=$(find / -name geogrid.exe -not -path "/proc/*" -not -path "/work/*" 2>/dev/null | head -1)
WPS=$(dirname "$GEOGRID")
# geogrid.exe may sit in WPS/geogrid/src (the link at WPS's top is what the tables sit beside).
[ -d "$WPS/geogrid" ] || WPS=$(dirname "$(dirname "$(dirname "$GEOGRID")")")
log "WPS at $WPS"
mkdir -p /work/wps && cd /work/wps
rm -f geogrid.exe ungrib.exe metgrid.exe GRIBFILE.* FILE:* met_em.* geo_em.* Vtable
for exe in geogrid ungrib metgrid; do
  ln -sf "$(find "$WPS" -name "$exe.exe" | head -1)" "$exe.exe"
done
ln -sf "$WPS/ungrib/Variable_Tables/Vtable.GFS" Vtable

cat > namelist.wps <<EOF
&share
 wrf_core = 'ARW',
 max_dom = 3,
 start_date = '$START', '$START', '$START',
 end_date   = '$END', '$END', '$END',
 interval_seconds = 10800,
 io_form_geogrid = 2,
/
&geogrid
 parent_id         = 1, 1, 2,
 parent_grid_ratio = 1, 3, 3,
 i_parent_start    = 1, 31, 41,
 j_parent_start    = 1, 26, 31,
 e_we              = 100, 121, 121,
 e_sn              = 80, 91, 91,
 geog_data_res = 'default', 'default', 'default',
 dx = 9000,
 dy = 9000,
 map_proj  = 'mercator',
 ref_lat   = 36.0,
 ref_lon   = -5.6,
 truelat1  = 36.0,
 stand_lon = -5.6,
 geog_data_path = '/geog/WPS_GEOG/',
 opt_geogrid_tbl_path = '$WPS/geogrid/',
/
&ungrib
 out_format = 'WPS',
 prefix = 'FILE',
/
&metgrid
 fg_name = 'FILE',
 io_form_metgrid = 2,
 opt_metgrid_tbl_path = '$WPS/metgrid/',
/
EOF

# The GFS files as GRIBFILE.AAA, .AAB, ... (link_grib.csh without csh).
letters=({A..Z})
i=0
for f in /work/gfs/gfs.*; do
  a=${letters[$((i / 676 % 26))]}; b=${letters[$((i / 26 % 26))]}; c=${letters[$((i % 26))]}
  ln -sf "$f" "GRIBFILE.$a$b$c"
  i=$((i + 1))
done

timed geogrid ./geogrid.exe > geogrid.log 2>&1 || { tail -30 geogrid.log; exit 1; }
timed ungrib ./ungrib.exe > ungrib.log 2>&1 || { tail -30 ungrib.log; exit 1; }
timed metgrid ./metgrid.exe > metgrid.log 2>&1 || { tail -30 metgrid.log; exit 1; }
ls -la met_em.* geo_em.*
