"""The Mac mini's beat for the ops panel (the owner, 23.10.2026: "the Mac's runs and CPU/memory in its state file, the
panel reading it"): every 2 minutes (launchd com.meteogust.beat) its CPU (WRF's programs apart), memory, the WRF run
going (stage, progress) and its last runs (wrf_state.py's history) to files.meteogust.com/runner/mac.json, with
INGEST_TOKEN from ~/.meteogust.env. The Tower's panel server reads it every minute and keeps the samples for the
Mac's band. Apart from runner/wrf-<region>.json on purpose: the beat's fresh time would read as the Mac answering for
a run there (tower.sh's fallback check). Standard library and macOS's own tools only.

    python3 wrf_poc/mac_beat.py [region]        (tarifa)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wrf_state  # noqa: E402

UPLOAD = "https://data.meteogust.com/tiles/upload/runner/mac.json"
# WRF's programs (and its MPI launcher): their CPU is the band's purple, the rest the Mac's other work.
WRF_PROGRAMS = re.compile(r"(^|/)(wrf|real|geogrid|ungrib|metgrid)\.exe$|(^|/)(mpirun|prterun|prted)$")


def token():
    if os.environ.get("INGEST_TOKEN"):
        return os.environ["INGEST_TOKEN"].strip()
    try:
        with open(os.path.expanduser("~/.meteogust.env")) as f:
            for line in f:
                if line.startswith("INGEST_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def cpu(cores):
    """(WRF's, the rest's) CPU in % of the whole machine (ps: each process's recent share of one core)."""
    out = subprocess.run(["ps", "-A", "-o", "%cpu=,comm="], capture_output=True, text=True).stdout
    wrf = other = 0.0
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pct = float(parts[0])
        except ValueError:
            continue
        if WRF_PROGRAMS.search(parts[1]):
            wrf += pct
        elif not parts[1].endswith("/ps"):
            other += pct
    return round(min(wrf / cores, 100), 1), round(min(other / cores, 100), 1)


def memory():
    """(used, total) GB: active, wired and compressed pages, as Activity Monitor's "Memory Used" roughly."""
    total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip()) / 2**30
    text = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    page = int(re.search(r"page size of (\d+) bytes", text).group(1))

    def pages(name):
        m = re.search(name + r":\s+(\d+)", text)
        return int(m.group(1)) if m else 0
    used = (pages("Pages active") + pages("Pages wired down") + pages("Pages occupied by compressor")) * page / 2**30
    return round(used, 2), round(total, 1)


def wrf_now(region):
    """The run going (from wrf_state.py's local state), with the model's progress while wrf.exe runs; None when idle."""
    try:
        with open(wrf_state.LOCAL.format(region=region)) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    if state.get("stage") in ("done", "failed") or time.time() - state.get("t", 0) > wrf_state.STALE:
        return None
    out = {k: state.get(k) for k in ("stage", "cycle", "started", "at")}
    out["running"] = state.get("stage") == "wrf"
    out["waiting"] = state.get("stage") == "waiting"
    if out["running"]:
        out["computing_from"] = (state.get("at") or {}).get("wrf")
        work = os.path.expanduser(f"~/wrf-work/{region}")
        try:
            env = dict(line.strip().split("=", 1) for line in open(f"{work}/env") if "=" in line)
            start, end = (datetime.strptime(env[k], "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc) for k in ("START", "END"))
            with open(f"{work}/wrf/rsl.out.0000", "rb") as f:
                f.seek(max(os.path.getsize(f"{work}/wrf/rsl.out.0000") - 4000, 0))
                last = re.findall(r"time (\d{4}-\d\d-\d\d_\d\d:\d\d:\d\d) on domain\s+1", f.read().decode(errors="replace"))
            if last:
                t = datetime.strptime(last[-1], "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc)
                out["progress"] = round(max(0.0, min(1.0, (t - start) / (end - start))), 3)
                out["model_time"] = t.strftime("%d %H:%M")
        except (OSError, KeyError, ValueError):
            pass
    return out


def main(region):
    cores = os.cpu_count() or 10
    cpu_wrf, cpu_other = cpu(cores)
    used, total = memory()
    body = {"t": round(time.time()), "host": os.uname().nodename, "cores": cores, "mem_total": total, "mem_used": used,
            "cpu_wrf": cpu_wrf, "cpu_other": cpu_other, "wrf": wrf_now(region), "runs": wrf_state.history(region)}
    key = token()
    if not key:
        sys.exit("no INGEST_TOKEN (~/.meteogust.env)")
    request = urllib.request.Request(UPLOAD, data=json.dumps(body, separators=(",", ":")).encode(), method="PUT",
                                     headers={"Authorization": "Bearer " + key, **wrf_state.AGENT})
    with urllib.request.urlopen(request, timeout=30):
        pass


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tarifa")
