#!/bin/bash
# The WRF test's model run inside NCAR's i-WRF container (wrf-poc.yml): real.exe (the start and
# the boundaries from the met_em files), then wrf.exe over the boxes (9, 3 and 1 km) on every
# core. NLEV and NSOIL (the met_em files' levels) come from /work/env, read on the runner.
set -euo pipefail
# The folders: the container's by default; the Mac mini passes its own (mac_wrf.sh, 23.10.2026).
WORK=${WRF_WORKDIR:-/work}
POCDIR=${WRF_POCDIR:-/poc}
source "$WORK/env"
source "$POCDIR/regions/$REGION.env"
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
# The domains: one size per box in the region's E_WE (9, 3 and 1 km); PARENT_ID their parents in WPS's numbering,
# "1, 1, 2" (three nested) unless the region adds a second 1 km box beside the first (Cape Town's Breede valley, the
# owner, 02.11.2026). The per-domain lists for N boxes: each (one value for all), nested (the outer box's, then the
# others'), levels (by box: 9 km, 3 km, then the 1 km ones, all inside the 3 km one).
PARENT_ID=${PARENT_ID:-"1, 1, 2"}
N=$(echo "$E_WE" | tr ',' '\n' | grep -c '[0-9]')
each() { local out=$1 k; for ((k = 1; k < N; k++)); do out="$out, $1"; done; echo "$out"; }
nested() { local out=$1 k; for ((k = 1; k < N; k++)); do out="$out, $2"; done; echo "$out"; }
levels() { local out="$1, $2" k; for ((k = 2; k < N; k++)); do out="$out, $3"; done; echo "$out"; }
timed() { local name=$1; shift; local t0; t0=$(date +%s); "$@" || return $?; echo "$name $(( $(date +%s) - t0 ))" >> "$WORK/times.txt"; log "$name done"; }
# The container's own set-up (its entrypoint is bypassed): Intel's MPI and compilers, the user's paths.
set +u
[ -f /opt/intel/oneapi/setvars.sh ] && source /opt/intel/oneapi/setvars.sh --force > /dev/null 2>&1 || true
[ -f /home/wrfuser/.bashrc ] && source /home/wrfuser/.bashrc > /dev/null 2>&1 || true
set -u
# The libraries the programs miss (the image sets no library path: NetCDF, Intel's runtime):
# each one found in the image and its folder added to LD_LIBRARY_PATH.
libraries() {
  command -v ldd > /dev/null || return 0  # macOS has none (and needs none: Homebrew's libraries)
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
ulimit -s unlimited 2> /dev/null || ulimit -s hard 2> /dev/null || true  # macOS: 64 MB at most

# WRF_RUNDIR: WRF's run folder (the Mac's ~/wrf/WRF/run); else found in the container.
WRFEXE=${WRF_RUNDIR:+$WRF_RUNDIR/wrf.exe}
[ -n "$WRFEXE" ] || WRFEXE=$(find / -name wrf.exe -path "*run*" -not -path "/proc/*" -not -path "/work/*" 2>/dev/null | head -1)
[ -n "$WRFEXE" ] || WRFEXE=$(find / -name wrf.exe -not -path "/proc/*" -not -path "/work/*" 2>/dev/null | head -1)
RUN=$(dirname "$WRFEXE")
# The tables WRF reads sit in WRF/run (the executables there are links to WRF/main).
[ -f "$RUN/RRTMG_LW_DATA" ] || RUN=$(dirname "$(dirname "$WRFEXE")")/run
log "WRF at $RUN"
libraries "$RUN"/real.exe "$RUN"/wrf.exe
mkdir -p "$WORK/wrf" && cd "$WORK/wrf"
for f in "$RUN"/*; do ln -sf "$f" .; done
rm -f namelist.input met_em.* wrfout_* wrfinput_* wrfbdy_* rsl.*
ln -sf "$WORK"/wps/met_em.* .

y() { echo "${1:0:4}"; }; m() { echo "${1:5:2}"; }; d() { echo "${1:8:2}"; }; h() { echo "${1:11:2}"; }
plus() { date -u -d "${1:0:10} ${1:11:8} UTC + $2 hours" +%Y-%m-%d_%H:00:00; }
# The 1 km box only for some hours (the owner, 17.10.2026: the nights don't need it, and it is about
# 88% of the computing): D3_FROM and D3_TO, hours from the start (3s: the met_em files' hours); the
# 3 km box goes on round it. Unset: the whole run.
D3S=$START; D3E=$END
[ -n "${D3_FROM:-}" ] && D3S=$(plus "$START" "$D3_FROM")
[ -n "${D3_TO:-}" ] && D3E=$(plus "$START" "$D3_TO")
log "1 km box from $D3S to $D3E"
# Each box's start and end: the 9 and 3 km boxes the whole run, every 1 km box its hours (D3S, D3E).
span() { local fn=$1 whole=$2 part=$3 out k; out="$($fn "$whole"), $($fn "$whole")"; for ((k = 2; k < N; k++)); do out="$out, $($fn "$part")"; done; echo "$out"; }
# Adaptive time steps (ADAPTIVE=1): longer steps where the wind allows (WRF keeps the CFL under 1.2).
ADAPT=""
if [ "${ADAPTIVE:-0}" = "1" ]; then
  ADAPT=" use_adaptive_time_step = .true.,
 step_to_output_time = .true.,
 target_cfl = $(each 1.2),
 target_hcfl = $(each .84),
 max_step_increase_pct = $(nested 5 51),
 starting_time_step = $(each -1),
 max_time_step = $(each -1),
 min_time_step = $(each -1),
 adaptation_domain = 1,"
  log "adaptive time steps"
fi
# The boundary layer (the owner, 19.10.2026: our wind too strong over the land round Tarifa, the calm
# valleys and the lee missing): YSU with its surface layer (revised MM5) and topo_wind=1 (Jimenez and
# Dudhia 2012: the hills a 1 km grid smooths away slow the wind in the valleys and on flat land and speed
# it up on the tops; needs VAR_SSO, from geogrid's mandatory data) instead of the CONUS suite's MYJ.
cat > namelist.input <<EOF
&time_control
 run_days = 0,
 run_hours = $HOURS,
 run_minutes = 0,
 run_seconds = 0,
 start_year  = $(span y $START $D3S),
 start_month = $(span m $START $D3S),
 start_day   = $(span d $START $D3S),
 start_hour  = $(span h $START $D3S),
 end_year    = $(span y $END $D3E),
 end_month   = $(span m $END $D3E),
 end_day     = $(span d $END $D3E),
 end_hour    = $(span h $END $D3E),
 interval_seconds = 10800,
 input_from_file = $(each .true.),
 history_interval = $(each 60),
 nwp_diagnostics = 1,
 frames_per_outfile = $(each 1000),
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
 max_dom = $N,
 e_we = $E_WE,
 e_sn = $E_SN,
 e_vert = $(each 40),
 p_top_requested = 5000,
 num_metgrid_levels = $NLEV,
 num_metgrid_soil_levels = $NSOIL,
 dx = $(levels 9000 3000 1000),
 dy = $(levels 9000 3000 1000),
 grid_id = $(seq -s ', ' 1 $N),
 parent_id = 0, ${PARENT_ID#*, },
 i_parent_start = $I_PARENT,
 j_parent_start = $J_PARENT,
 parent_grid_ratio = $(nested 1 3),
 parent_time_step_ratio = $(nested 1 3),
 feedback = 1,
 smooth_option = 0,
/
&physics
 physics_suite = 'CONUS',
 mp_physics = $(each -1),
 cu_physics = $(nested -1 0),
 ra_lw_physics = $(each -1),
 ra_sw_physics = $(each -1),
 bl_pbl_physics = $(each 1),
 sf_sfclay_physics = $(each 1),
 topo_wind = $(each 1),
 sf_surface_physics = $(each -1),
 radt = $(each 9),
 bldt = $(each 0),
 cudt = $(each 0),
 icloud = 1,
 num_land_cat = 21,
 sf_urban_physics = $(each 0),
 fractional_seaice = 1,
/
&fdda
/
&dynamics
 hybrid_opt = 2,
 w_damping = 1,
 diff_opt = $(each 2),
 km_opt = $(each 4),
 diff_6th_opt = $(each 0),
 diff_6th_factor = $(each 0.12),
 base_temp = 290.,
 damp_opt = 3,
 zdamp = $(each 5000.),
 dampcoef = $(each 0.2),
 khdif = $(each 0),
 kvdif = $(each 0),
 non_hydrostatic = $(each .true.),
 moist_adv_opt = $(each 1),
 scalar_adv_opt = $(each 1),
 gwd_opt = $(nested 1 0),
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
