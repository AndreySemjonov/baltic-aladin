#!/usr/bin/env bash
# WRF benchmark on the Mac mini (the owner's OK, 23.10.2026): the Tower's Tarifa run of 23.10 06Z (its wrfinput and
# wrfbdy, namelist with the adaptive step), only 06-09Z with all three boxes, on N processes.
#   bash ~/wrf/bench.sh 4
export PATH=/opt/homebrew/bin:$PATH
N=${1:-4}
D=$HOME/wrf/bench-$N
rm -rf "$D" && mkdir -p "$D" && cd "$D" || exit 1
for f in "$HOME"/wrf/WRF/run/*; do ln -sf "$f" .; done
rm -f namelist.input
ln -sf "$HOME"/wrf/case/wrfinput_d0* "$HOME"/wrf/case/wrfbdy_d01 .
# The first 3 hours for every box (the Tower's run: d01/d02 48 h, d03 0-36 h).
/usr/bin/python3 - "$HOME/wrf/case/namelist.input" > namelist.input <<'EOF'
import re, sys
text = open(sys.argv[1]).read()
for key, value in (("run_hours", "3"), ("end_year", "2026, 2026, 2026"), ("end_month", "10, 10, 10"),
                   ("end_day", "23, 23, 23"), ("end_hour", "09, 09, 09")):
    text = re.sub(rf"^ {key}\s*=.*$", f" {key} = {value},", text, flags=re.M)
print(text, end="")
EOF
grep -E "run_hours|end_hour|end_day|use_adaptive" namelist.input
ulimit -s hard 2>/dev/null
start=$(date +%s)
mpirun -np "$N" ./wrf.exe > mpirun.log 2>&1
wall=$(( $(date +%s) - start ))
/usr/bin/python3 - "$N" "$wall" <<'EOF'
import re, sys
n, wall = sys.argv[1], int(sys.argv[2])
d01 = steps = 0.0
ok = False
for line in open("rsl.out.0000"):
    m = re.search(r"time (\S+) on domain\s+(\d):\s+([\d.]+) elapsed", line)
    if m:
        steps += 1
        if m.group(2) == "1":
            d01 += float(m.group(3))
    ok = ok or "SUCCESS COMPLETE WRF" in line
print(f"Mac, {n} processes: wall {wall} s, d01 steps (with the boxes inside) {d01:.0f} s, {int(steps)} steps, "
      f"{'SUCCESS' if ok else 'NOT COMPLETE'}")
EOF
