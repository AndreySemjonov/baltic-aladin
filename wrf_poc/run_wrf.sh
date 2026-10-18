#!/bin/bash
# The WRF test's model run inside NCAR's i-WRF container (wrf-poc.yml): real.exe (the start and
# the boundaries from the met_em files), then wrf.exe over the three boxes (9, 3 and 1 km) on every
# core. NLEV and NSOIL (the met_em files' levels) come from /work/env, read on the runner.
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
ulimit -s unlimited || true

WRFEXE=$(find / -name wrf.exe -path "*run*" -not -path "/proc/*" -not -path "/work/*" 2>/dev/null | head -1)
[ -n "$WRFEXE" ] || WRFEXE=$(find / -name wrf.exe -not -path "/proc/*" -not -path "/work/*" 2>/dev/null | head -1)
RUN=$(dirname "$WRFEXE")
# The tables WRF reads sit in WRF/run (the executables there are links to WRF/main).
[ -f "$RUN/RRTMG_LW_DATA" ] || RUN=$(dirname "$(dirname "$WRFEXE")")/run
log "WRF at $RUN"
libraries "$RUN"/real.exe "$RUN"/wrf.exe
mkdir -p /work/wrf && cd /work/wrf
for f in "$RUN"/*; do ln -sf "$f" .; done
rm -f namelist.input met_em.* wrfout_* wrfinput_* wrfbdy_* rsl.*
ln -sf /work/wps/met_em.* .

y() { echo "${1:0:4}"; }; m() { echo "${1:5:2}"; }; d() { echo "${1:8:2}"; }; h() { echo "${1:11:2}"; }
plus() { date -u -d "${1:0:10} ${1:11:8} UTC + $2 hours" +%Y-%m-%d_%H:00:00; }
# The 1 km box only for some hours (the owner, 17.10.2026: the nights don't need it, and it is about
# 88% of the computing): D3_FROM and D3_TO, hours from the start (3s: the met_em files' hours); the
# 3 km box goes on round it. Unset: the whole run.
D3S=$START; D3E=$END
[ -n "${D3_FROM:-}" ] && D3S=$(plus "$START" "$D3_FROM")
[ -n "${D3_TO:-}" ] && D3E=$(plus "$START" "$D3_TO")
log "1 km box from $D3S to $D3E"
# Adaptive time steps (ADAPTIVE=1): longer steps where the wind allows (WRF keeps the CFL under 1.2).
ADAPT=""
if [ "${ADAPTIVE:-0}" = "1" ]; then
  ADAPT=" use_adaptive_time_step = .true.,
 step_to_output_time = .true.,
 target_cfl = 1.2, 1.2, 1.2,
 target_hcfl = .84, .84, .84,
 max_step_increase_pct = 5, 51, 51,
 starting_time_step = -1, -1, -1,
 max_time_step = -1, -1, -1,
 min_time_step = -1, -1, -1,
 adaptation_domain = 1,"
  log "adaptive time steps"
fi
cat > namelist.input <<EOF
&time_control
 run_days = 0,
 run_hours = $HOURS,
 run_minutes = 0,
 run_seconds = 0,
 start_year  = $(y $START), $(y $START), $(y $D3S),
 start_month = $(m $START), $(m $START), $(m $D3S),
 start_day   = $(d $START), $(d $START), $(d $D3S),
 start_hour  = $(h $START), $(h $START), $(h $D3S),
 end_year    = $(y $END), $(y $END), $(y $D3E),
 end_month   = $(m $END), $(m $END), $(m $D3E),
 end_day     = $(d $END), $(d $END), $(d $D3E),
 end_hour    = $(h $END), $(h $END), $(h $D3E),
 interval_seconds = 10800,
 input_from_file = .true., .true., .true.,
 history_interval = 60, 60, 60,
 nwp_diagnostics = 1,
 frames_per_outfile = 1000, 1000, 1000,
 restart = .false.,
 restart_interval = 100000,
 io_form_history = 2,
 io_form_restart = 2,
 io_form_input = 2,
 io_form_boundary = 2,
/
&domains
 time_step = 45,
 time_step_fract_num = 0,
 time_step_fract_den = 1,
$ADAPT
 max_dom = 3,
 e_we = $E_WE,
 e_sn = $E_SN,
 e_vert = 40, 40, 40,
 p_top_requested = 5000,
 num_metgrid_levels = $NLEV,
 num_metgrid_soil_levels = $NSOIL,
 dx = 9000, 3000, 1000,
 dy = 9000, 3000, 1000,
 grid_id = 1, 2, 3,
 parent_id = 0, 1, 2,
 i_parent_start = $I_PARENT,
 j_parent_start = $J_PARENT,
 parent_grid_ratio = 1, 3, 3,
 parent_time_step_ratio = 1, 3, 3,
 feedback = 1,
 smooth_option = 0,
/
&physics
 physics_suite = 'CONUS',
 mp_physics = -1, -1, -1,
 cu_physics = -1, 0, 0,
 ra_lw_physics = -1, -1, -1,
 ra_sw_physics = -1, -1, -1,
 bl_pbl_physics = -1, -1, -1,
 sf_sfclay_physics = -1, -1, -1,
 sf_surface_physics = -1, -1, -1,
 radt = 9, 9, 9,
 bldt = 0, 0, 0,
 cudt = 0, 0, 0,
 icloud = 1,
 num_land_cat = 21,
 sf_urban_physics = 0, 0, 0,
 fractional_seaice = 1,
/
&fdda
/
&dynamics
 hybrid_opt = 2,
 w_damping = 1,
 diff_opt = 2, 2, 2,
 km_opt = 4, 4, 4,
 diff_6th_opt = 0, 0, 0,
 diff_6th_factor = 0.12, 0.12, 0.12,
 base_temp = 290.,
 damp_opt = 3,
 zdamp = 5000., 5000., 5000.,
 dampcoef = 0.2, 0.2, 0.2,
 khdif = 0, 0, 0,
 kvdif = 0, 0, 0,
 non_hydrostatic = .true., .true., .true.,
 moist_adv_opt = 1, 1, 1,
 scalar_adv_opt = 1, 1, 1,
 gwd_opt = 1, 0, 0,
/
&bdy_control
 spec_bdy_width = 5,
 specified = .true.,
/
&grib2
/
&namelist_quilt
 nio_tasks_per_group = 0,
 nio_groups = 1,
/
EOF

# WRF_CORES: fewer processes than threads (the Tower's i7-12700K: 8 fast cores with two threads each
# and 4 slow ones; WRF waits on its slowest piece, 18.10.2026).
NP=${WRF_CORES:-$(nproc)}
MPI=$(command -v mpirun || command -v mpiexec || find / -name mpirun -not -path "/proc/*" 2>/dev/null | head -1 || true)
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
log "$NP cores, MPI: ${MPI:-none}"
EXTRA=""
if [ -n "$MPI" ] && "$MPI" --version 2>&1 | grep -qi "open mpi"; then EXTRA="--oversubscribe"; fi
run() {
  if [ -n "$MPI" ]; then "$MPI" -np "$1" $EXTRA "${@:2}"; else "${@:2}"; fi
}
timed real run 1 ./real.exe > real.log 2>&1 || { tail -40 real.log rsl.error.0000 2>/dev/null; exit 1; }
grep -q "SUCCESS COMPLETE REAL" rsl.error.0000 real.log 2>/dev/null || { tail -40 rsl.error.0000 real.log 2>/dev/null; exit 1; }
timed wrf run "$NP" ./wrf.exe > wrf.log 2>&1 || { tail -60 rsl.error.0000 wrf.log 2>/dev/null; exit 1; }
tail -5 rsl.error.0000 || true
ls -la wrfout_*
