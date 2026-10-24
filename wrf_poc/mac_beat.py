"""The Mac mini's beat for the ops panel (the owner, 23.10.2026: "the Mac's runs and CPU/memory in its state file, the
panel reading it"): every 2 minutes (launchd com.meteogust.beat) its CPU (WRF's programs apart), memory, the WRF run
going (stage, progress) and its last runs (wrf_state.py's history) to files.meteogust.com/runner/mac.json, with
INGEST_TOKEN from ~/.meteogust.env. The Tower's panel server reads it every minute and keeps the samples for the
Mac's band. Apart from runner/wrf-<region>.json on purpose: the beat's fresh time would read as the Mac answering for
a run there (tower.sh's fallback check). Standard library and macOS's own tools only.

Also (the owner, 25.10.2026: WRF's run card like the other jobs', "with the Mac beat extended for WRF memory and
network"): WRF's own memory (its programs' resident memory, "mem_wrf" MB) and the Mac's network in and out ("net_in",
"net_out" MB/s over its Ethernet and Wi-Fi since the last beat, the counters kept in ~/.meteogust-beat-net.json).

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
    """(WRF's, the rest's) CPU in % of the whole machine (ps: each process's recent share of one core), and WRF's
    programs' resident memory (MB)."""
    out = subprocess.run(["ps", "-A", "-o", "%cpu=,rss=,comm="], capture_output=True, text=True).stdout
    wrf = other = 0.0
    wrf_kb = 0
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pct, rss = float(parts[0]), int(parts[1])
        except ValueError:
            continue
        if WRF_PROGRAMS.search(parts[2]):
            wrf += pct
            wrf_kb += rss
        elif not parts[2].endswith("/ps"):
            other += pct
    return round(min(wrf / cores, 100), 1), round(min(other / cores, 100), 1), round(wrf_kb / 1024)


NET_STATE = os.path.expanduser("~/.meteogust-beat-net.json")


def network():
    """(in, out) MB/s over the Mac's Ethernet and Wi-Fi (netstat -ib's byte counters, the en* interfaces' link rows)
    since the last beat; (None, None) on the first beat or when the counters went back (a restart)."""
    rx = tx = 0
    text = subprocess.run(["netstat", "-ib"], capture_output=True, text=True).stdout
    for line in text.splitlines()[1:]:
        fields = line.split()
        # A link row's columns from the right: ... Ibytes Opkts Oerrs Obytes Coll (an empty Address shifts the left).
        if len(fields) >= 7 and fields[0].startswith("en") and "<Link#" in line:
            try:
                rx, tx = rx + int(fields[-5]), tx + int(fields[-2])
            except ValueError:
                pass
    now = time.time()
    try:
        with open(NET_STATE) as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = None
    try:
        with open(NET_STATE, "w") as f:
            json.dump({"t": now, "rx": rx, "tx": tx}, f)
    except OSError:
        pass
    if not last or now <= last["t"] or rx < last["rx"] or tx < last["tx"]:
        return None, None
    dt = now - last["t"]
    return round((rx - last["rx"]) / dt / 1e6, 2), round((tx - last["tx"]) / dt / 1e6, 2)


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
    cpu_wrf, cpu_other, mem_wrf = cpu(cores)
    used, total = memory()
    net_in, net_out = network()
    body = {"t": round(time.time()), "host": os.uname().nodename, "cores": cores, "mem_total": total, "mem_used": used,
            "cpu_wrf": cpu_wrf, "cpu_other": cpu_other, "mem_wrf": mem_wrf, "net_in": net_in, "net_out": net_out,
            "wrf": wrf_now(region), "runs": wrf_state.history(region)}
    key = token()
    if not key:
        sys.exit("no INGEST_TOKEN (~/.meteogust.env)")
    request = urllib.request.Request(UPLOAD, data=json.dumps(body, separators=(",", ":")).encode(), method="PUT",
                                     headers={"Authorization": "Bearer " + key, **wrf_state.AGENT})
    with urllib.request.urlopen(request, timeout=30):
        pass


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tarifa")
