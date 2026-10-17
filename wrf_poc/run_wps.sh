#!/bin/bash
# The WRF test's preprocessing inside NCAR's i-WRF container (wrf-poc.yml): geogrid (the terrain of
# three nested boxes: 9, 3 and 1 km, the 1 km one Cádiz to Málaga with Tangier, tilted about 10°), ungrib (the GFS files), metgrid (GFS onto the
# boxes). Each step timed into /work/times.txt. START and END come from /work/env.
set -euo pipefail
source /work/env
source "/poc/regions/$REGION.env"
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
timed() { local name=$1; shift; local t0; t0=$(date +%s); "$@" || return $?; echo "$name $(( $(date +%s) - t0 ))" >> /work/times.txt; log "$name done"; }
# The container's own set-up (its entrypoint is bypassed): Intel's MPI and compilers, the user's paths.
set +u
[ -f /opt/intel/oneapi/setvars.sh ] && source /opt/intel/oneapi/setvars.sh --force > /dev/null 2>&1 || true
[ -f /home/wrfuser/.bashrc ] && source /home/wrfuser/.bashrc > /dev/null 2>&1 || true
set -u
# The libraries the programs miss (the image sets no library path: NetCDF, Intel's runtime):
# each one found in the image and its folder added to LD_LIBRARY_PATH.
libraries() {
  local extra="" lib dir
  for exe in "$@"; do
    for lib in $(ldd "$exe" 2>/dev/null | awk '/not found/ {print $1}'); do
      dir=$(dirname "$(find / -name "$lib" -not -path "/proc/*" -not -path "/work/*" 2>/dev/null | head -1)")
      if [ "$dir" != "." ] && [[ ":$extra:" != *":$dir:"* ]]; then extra="$extra:$dir"; fi
    done
  done
  export LD_LIBRARY_PATH="${extra#:}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  log "library path: $LD_LIBRARY_PATH"
  for exe in "$@"; do ldd "$exe" | grep "not found" && log "still missing for $exe" || true; done
}

GEOGRID=$(find / -name geogrid.exe -not -path "/proc/*" -not -path "/work/*" 2>/dev/null | head -1)
WPS=$(dirname "$GEOGRID")
# geogrid.exe may sit in WPS/geogrid/src (the link at WPS's top is what the tables sit beside).
[ -d "$WPS/geogrid" ] || WPS=$(dirname "$(dirname "$(dirname "$GEOGRID")")")
log "WPS at $WPS"
libraries "$WPS"/geogrid/src/geogrid.exe "$WPS"/ungrib/src/ungrib.exe "$WPS"/metgrid/src/metgrid.exe
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
 i_parent_start    = $I_PARENT,
 j_parent_start    = $J_PARENT,
 e_we              = $E_WE,
 e_sn              = $E_SN,
 geog_data_res = 'default', 'default', 'default',
 dx = 9000,
 dy = 9000,
 map_proj  = 'lambert',
 ref_lat   = $REF_LAT,
 ref_lon   = $REF_LON,
 truelat1  = $TRUELAT1,
 truelat2  = $TRUELAT2,
 stand_lon = $STAND_LON,
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
