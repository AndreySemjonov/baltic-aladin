"""The ops panel's server (the owner, 21.10.2026: "admin panel what will run all the time on mac mini... updates
all the time in real time... what downloads, what speeds, what schedule"; read-only; docs/mockups/ops-panel-
2026-10-21.html in the app's repo). Plain Python, nothing to install; the scheduler starts it when it isn't running,
and it reloads itself when this file changes.

- Every 2 seconds while someone watches (otherwise every 30): each thread's CPU, the memory, the line's traffic,
  and each container's CPU, memory and traffic (the jobs', WRF's; Docker's cgroup files and /proc).
- The jobs' downloads as they happen (runner/ops/hook/sitecustomize.py sends them over UDP): the file, its speed,
  the bytes per source today.
- The scheduler's own view (LIVE: running, waiting and why, next and when), its records (STATS/_runs.jsonl, each
  job's measurements, the models' arrivals), WRF's lock and log, GitHub's times for the same jobs (its public API,
  once an hour).

Pages: / (index.html), /api/fast and /api/slow (JSON), /api/stream (Server-Sent Events: fast every 2 s, slow every
30 s), /api/log?job=<name> (a job's log, live), /api/usage?job=<name>&start=<t> (a run's CPU, memory and download
over its run, measure.py's samples). Listens on 127.0.0.1 only: from outside through the Cloudflare
Tunnel, and a request that came through Cloudflare without Access's sign-in in front is refused.

    python3 runner/ops/server.py       (MG_HOME, MG_OPS_PORT 8090, MG_OPS_UDP 8091)
"""
import fcntl
import glob
import http.server
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import views  # noqa: E402  (freshness, capacity, admin)
import adminproxy  # noqa: E402  (admin.meteogust.com through the panel)

HOME = os.environ.get("MG_HOME", "/var/lib/meteogust")
PORT = int(os.environ.get("MG_OPS_PORT", "8090"))
UDP = int(os.environ.get("MG_OPS_UDP", "8091"))
STATS, LOGS, LIVE = f"{HOME}/stats", f"{HOME}/logs", f"{HOME}/live.json"
WRF_LOGS, WRF_LOCK = "/root/wrf-logs", "/root/wrf-logs/.lock"
HERE = os.path.dirname(os.path.abspath(__file__))
RIGA = ZoneInfo("Europe/Riga")
# The Tower's WRF tasks (Riga time; moved out of the 00Z/12Z peaks on 21.10.2026, then W1: start early and wait for
# GFS's 06Z/18Z hours, so the run itself begins when NOAA has them, about 3 h 40 after the cycle).
WRF_AT = [(11, 45), (23, 45)]
GITHUB = "https://api.github.com/repos/AndreySemjonov/baltic-aladin/actions/workflows/"
AGENT = {"User-Agent": "MeteoGust ops panel (data.meteogust.com)"}
# Each job's GitHub workflow (and the last word of the run's title where one workflow runs several), for "On GitHub".
GITHUB_OF = {
    "world-gfs": ("world.yml", "gfs"), "world-ecmwf": ("world.yml", "ecmwf"), "world-icon": ("world.yml", "icon"),
    "icon-eu": ("world.yml", "icon-eu"), "world-gdps": ("world.yml", "gdps"), "ewam": ("world.yml", "ewam"),
    "ukmo": ("ukmo.yml", None), "nordic-1km": ("nordic.yml", None), "mix": ("mix.yml", None),
    "mix-world": ("mix-world.yml", None), "series": ("series.yml", None), "spot-points": ("spot-points.yml", None),
    "gfs013": ("gfs013.yml", None), "ecmwf-short": ("ecmwf-short.yml", None), "icon-eu-short": ("icon-eu-short.yml", None),
    "ens": ("ens.yml", None), "chance": ("chance.yml", None), "rain-now": ("rain-now.yml", None), "air": ("air.yml", None),
    "sea": ("sea.yml", None), "waves": ("waves.yml", None), "sea-world": ("sea-world.yml", None),
}
LANES = [
    ("WRF", lambda j: j == "wrf"),
    ("World models", lambda j: j.startswith("world-") or j in ("ecmwf-waves", "ewam")),
    ("Europe models", lambda j: j == "icon-eu" or j.startswith("bucket-") or j == "ukmo"),
    ("Regional", lambda j: j.startswith("regional-") or j in ("harm-fi", "aladin")),
    ("MET Nordic 1 km", lambda j: j.startswith("nordic-")),
    ("Mixes", lambda j: j in ("mix", "mix-world") or j.startswith("weather-mix")),
    ("Spots", lambda j: j in ("series", "spot-points", "gfs013", "ecmwf-short", "icon-eu-short", "ens")),
    ("Rain, air, seas", lambda j: True),
]


def lane(job):
    return next(name for name, test in LANES if test(job))


def log(text):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {text}", flush=True)


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def jsonl(path, last=500):
    try:
        with open(path) as f:
            lines = f.readlines()[-last:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def host_of(url):
    host = urllib.parse.urlsplit(url).hostname or "?"
    if host == "files.meteogust.com" and "/mirror/" in url:
        return "Our copy (R2)"
    names = {"files.meteogust.com": "Our file store", "openmeteo.s3.amazonaws.com": "Open-Meteo (Oregon)",
             "opendata.dwd.de": "DWD open data", "thredds.met.no": "met.no", "data.ecmwf.int": "ECMWF (Bologna)",
             "storage.googleapis.com": "Google Cloud", "noaa-gfs-bdp-pds.s3.amazonaws.com": "NOAA (AWS)",
             "data.meteogust.com": "Our server", "api.github.com": "GitHub"}
    if host in names:
        return names[host]
    if host.startswith("ecmwf-forecasts") or "ecmwf" in host:
        return "ECMWF (AWS Frankfurt)" if "amazonaws" in host else "ECMWF"
    return host


# MARK: Downloads (UDP from the jobs)

class Downloads:
    def __init__(self):
        self.lock = threading.Lock()
        self.open = {}          # (job, url) -> {"b", "t0", "last", "host"}
        self.current = {}       # job -> {"url", "host", "b", "s", "speed", "t"}
        self.window = deque()   # (time, host, bytes) for the rates now
        self.day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.hosts = {}         # host -> {"bytes", "best"}
        # Each job's run so far: its files, bytes and the spans it was downloading (the Pipeline's popups, the owner,
        # 22.10.2026: "available time, waited time, download time, when started"); written to _downloads.jsonl when
        # the run ends (sync).
        self.acc = {}           # job -> {"files", "bytes", "spans": [[t0, t1]], "start"}
        # The runs going, kept across the server's reloads (each push of its code reloads it: a run going then lost its
        # downloads, about a quarter of 22.10.2026's showed "not recorded"); saved by sync().
        try:
            kept = json.loads(read(f"{STATS}/_downloads_open.json", "{}") or "{}")
            if time.time() - kept.get("t", 0) < 3600:
                self.acc = kept.get("acc") or {}
        except (ValueError, AttributeError):
            self.acc = {}
        saved = json.loads(read(f"{STATS}/_sources.json", "{}") or "{}")
        if saved.get("day") == self.day:
            self.hosts = saved.get("hosts", {})
        self.saved = time.time()

    def message(self, m):
        job, url, event, size, seconds = m.get("j", "?"), m.get("u", ""), m.get("e"), int(m.get("b") or 0), m.get("s") or 0
        now = time.time()
        host = host_of(url)
        with self.lock:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if day != self.day:
                self.day, self.hosts = day, {}
            h = self.hosts.setdefault("Uploads to our file store" if event == "up" else host, {"bytes": 0, "best": 0})
            if event == "up":
                h["bytes"] += size
                self.window.append((now, "Uploads to our file store", size))
                return
            key = (job, url)
            if event == "start":
                self.open[key] = {"b": 0, "t0": now, "host": host}
            entry = self.open.setdefault(key, {"b": 0, "t0": now - seconds, "host": host})
            delta = max(size - entry["b"], 0)
            entry["b"] = size
            h["bytes"] += delta
            run = self.acc.setdefault(job, {"files": 0, "bytes": 0, "spans": [], "start": None})
            if "span" not in entry:
                entry["span"] = [now - seconds, now]
                run["spans"].append(entry["span"])
                run["files"] += 1
            entry["span"][1] = now
            run["bytes"] += delta
            if delta:
                self.window.append((now, host, delta))
            speed = size / seconds if seconds > 0.2 else 0
            # The best speed: a download of 4 MB or more (small ones are mostly waiting).
            if event == "end" and size >= 4e6 and speed > h["best"]:
                h["best"] = speed
            self.current[job] = {"url": url, "host": host, "b": size, "s": seconds, "speed": speed, "t": now,
                                 "done": event == "end"}
            if event == "end":
                self.open.pop(key, None)
            if now - self.saved > 300:
                self.saved = now
                try:
                    with open(f"{STATS}/_sources.json.tmp", "w") as f:
                        json.dump({"day": self.day, "hosts": self.hosts}, f)
                    os.replace(f"{STATS}/_sources.json.tmp", f"{STATS}/_sources.json")
                except OSError:
                    pass

    def rates(self, seconds=10):
        """Bytes a second per source over the last `seconds`."""
        now = time.time()
        with self.lock:
            while self.window and now - self.window[0][0] > 60:
                self.window.popleft()
            out = {}
            for t, host, b in self.window:
                if now - t <= seconds:
                    out[host] = out.get(host, 0) + b / seconds
            return out

    @staticmethod
    def busy(spans):
        """The time any download was going (the spans' union, seconds)."""
        total, end = 0.0, None
        for a, b in sorted(spans):
            if end is None or a > end:
                total += b - a
                end = b
            elif b > end:
                total += b - end
                end = b
        return total

    def summary(self, job):
        """A running job's downloads so far: files, MB, busy seconds, the first one's start."""
        with self.lock:
            run = self.acc.get(job)
            if not run or not run["spans"]:
                return None
            return {"files": run["files"], "mb": round(run["bytes"] / 1e6), "busy_s": round(self.busy(run["spans"])),
                    "first": round(min(a for a, _ in run["spans"]))}

    def sync(self, running):
        """Runs ended (or a new one begun) written to _downloads.jsonl; `running`: {job: its start}."""
        now, done = time.time(), []
        with self.lock:
            for job, run in list(self.acc.items()):
                started = running.get(job)
                if started and run["start"] is None:
                    run["start"] = started
                if started and run["start"] and abs(started - run["start"]) > 120:
                    done.append((job, run))
                    self.acc[job] = {"files": 0, "bytes": 0, "spans": [], "start": started}
                elif not started and (not run["spans"] or now - max(b for _, b in run["spans"]) > 90):
                    done.append((job, run))
                    del self.acc[job]
            keep = json.dumps({"t": round(now), "acc": self.acc})
        try:
            with open(f"{STATS}/_downloads_open.json.tmp", "w") as f:
                f.write(keep)
            os.replace(f"{STATS}/_downloads_open.json.tmp", f"{STATS}/_downloads_open.json")
        except OSError:
            pass
        for job, run in done:
            if run["start"] and run["spans"]:
                views.append(f"{STATS}/_downloads.jsonl", {"job": job, "start": round(run["start"]), "files": run["files"],
                                                           "mb": round(run["bytes"] / 1e6), "busy_s": round(self.busy(run["spans"])),
                                                           "first": round(min(a for a, _ in run["spans"])),
                                                           "last": round(max(b for _, b in run["spans"]))})

    def job_rates(self, seconds=6):
        """Each job's download speed now (bytes a second) and its current file."""
        now = time.time()
        with self.lock:
            out = {}
            for job, c in self.current.items():
                if now - c["t"] < 15:
                    out[job] = dict(c)
            return out

    def listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", UDP))
        while True:
            try:
                data, _ = sock.recvfrom(8192)
                self.message(json.loads(data))
            except Exception:
                pass


# MARK: The machine and the containers

class Machine:
    def __init__(self, downloads):
        self.downloads = downloads
        self.lock = threading.Lock()
        self.prev_cpu = None
        self.prev_net = None
        self.prev_cont = {}
        self.containers = {}    # id -> {"name", "image", "pid", "started"}
        self.last_ps = 0
        self.history = deque(maxlen=150)   # (t, in MB/s, out MB/s)
        self.job_speed = {}     # job -> deque of MB/s
        self.snapshot = {}
        self.watched = 0        # when someone last asked

    def cpu(self):
        out = []
        for line in (read("/proc/stat", "") or "").splitlines():
            if re.match(r"cpu\d+ ", line):
                v = [int(x) for x in line.split()[1:9]]
                out.append((sum(v), v[3] + v[4]))
        return out

    def net(self):
        rx = tx = 0
        rows = {}
        for line in (read("/proc/net/dev", "") or "").splitlines()[2:]:
            name, rest = line.split(":", 1)
            f = rest.split()
            rows[name.strip()] = (int(f[0]), int(f[8]))
        if "eth0" in rows:
            return rows["eth0"]
        for name, (r, t) in rows.items():
            if name != "lo" and not name.startswith(("docker", "veth", "br-")):
                rx, tx = rx + r, tx + t
        return rx, tx

    def refresh_containers(self):
        out = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.ID}}|{{.Names}}|{{.Image}}"],
                             capture_output=True, text=True, timeout=20).stdout
        seen = {}
        for line in out.splitlines():
            cid, name, image = line.split("|", 2)
            if cid in self.containers:
                seen[cid] = self.containers[cid]
                continue
            info = subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}|{{.State.StartedAt}}", cid],
                                  capture_output=True, text=True, timeout=20).stdout.strip()
            pid, started = (info.split("|") + [""])[:2]
            try:
                start = datetime.fromisoformat(started[:26].rstrip("Z") + "+00:00").timestamp()
            except ValueError:
                start = None
            seen[cid] = {"name": name, "image": image, "pid": int(pid or 0), "started": start}
        self.containers = seen

    def container_stats(self, cid, pid):
        cpu = read(f"/sys/fs/cgroup/cpuacct/docker/{cid}/cpuacct.usage")
        if cpu is None:
            cpu = read(f"/sys/fs/cgroup/docker/{cid}/cpu.stat")  # cgroups v2 (the Mac mini's Docker, if used)
            cpu = int(re.search(r"usage_usec (\d+)", cpu).group(1)) * 1000 if cpu else 0
        mem = 0
        stat = read(f"/sys/fs/cgroup/memory/docker/{cid}/memory.stat")
        if stat:
            m = re.search(r"^total_rss (\d+)", stat, re.M)
            mem = int(m.group(1)) if m else 0
        else:
            v2 = read(f"/sys/fs/cgroup/docker/{cid}/memory.stat")
            m = re.search(r"^anon (\d+)", v2 or "", re.M)
            mem = int(m.group(1)) if m else 0
        rx = 0
        if pid:
            for line in (read(f"/proc/{pid}/net/dev", "") or "").splitlines()[2:]:
                name, rest = line.split(":", 1)
                if name.strip() == "eth0":
                    rx = int(rest.split()[0])
        return int(cpu), mem, rx

    def sample(self):
        now = time.time()
        cores = []
        cpu = self.cpu()
        if self.prev_cpu and len(self.prev_cpu) == len(cpu):
            for (t1, i1), (t0, i0) in zip(cpu, self.prev_cpu):
                dt = t1 - t0
                cores.append(round(100 * (1 - (i1 - i0) / dt), 1) if dt > 0 else 0)
        self.prev_cpu = cpu
        mem = {}
        for line in (read("/proc/meminfo", "") or "").splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) * 1024
        rx, tx = self.net()
        rate_in = rate_out = 0
        if self.prev_net:
            dt = now - self.prev_net[2]
            rate_in, rate_out = (rx - self.prev_net[0]) / dt / 1e6, (tx - self.prev_net[1]) / dt / 1e6
        self.prev_net = (rx, tx, now)
        if now - self.last_ps > 10:
            self.last_ps = now
            try:
                self.refresh_containers()
            except Exception as error:
                log(f"docker ps: {error}")
        conts = []
        for cid, c in self.containers.items():
            try:
                cpu_ns, rss, crx = self.container_stats(cid, c["pid"])
            except Exception:
                continue
            prev = self.prev_cont.get(cid)
            cpu_pct = net_in = 0
            if prev:
                dt = now - prev[3]
                cpu_pct = (cpu_ns - prev[0]) / dt / 1e7
                net_in = max(crx - prev[2], 0) / dt / 1e6
            self.prev_cont[cid] = (cpu_ns, rss, crx, now)
            kind = "wrf" if "iwrf" in c["image"] or "wrf" in c["name"] else "job" if c["name"].startswith("mg-") else "other"
            conts.append({"name": c["name"][3:] if kind == "job" else c["name"], "kind": kind, "cpu": round(cpu_pct),
                          "mem": rss, "net_in": round(net_in, 2), "started": c["started"]})
        self.prev_cont = {k: v for k, v in self.prev_cont.items() if k in self.containers}
        with self.lock:
            self.history.append((round(now), round(rate_in, 2), round(rate_out, 2)))
            for c in conts:
                if c["kind"] == "job":
                    self.job_speed.setdefault(c["name"], deque(maxlen=30)).append(c["net_in"])
            for name in [n for n in self.job_speed if n not in {c["name"] for c in conts}]:
                del self.job_speed[name]
            self.snapshot = {"t": round(now), "cores": cores, "mem_total": mem.get("MemTotal", 0),
                             "mem_avail": mem.get("MemAvailable", 0), "net_in": round(rate_in, 2), "net_out": round(rate_out, 2),
                             "containers": conts}

    def run(self):
        while True:
            try:
                self.sample()
            except Exception as error:
                log(f"sample: {error}")
            time.sleep(2 if time.time() - self.watched < 60 else 30)


# MARK: WRF

def wrf_state():
    running = False
    if os.path.exists(WRF_LOCK):
        try:
            with open(WRF_LOCK) as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(f, fcntl.LOCK_UN)
                except OSError:
                    running = True
        except OSError:
            pass
    # The runs' own logs (<UTC start>Z-<region>.log; the folder has others, e.g. core8.log from the core tests).
    logs = sorted(p for p in glob.glob(f"{WRF_LOGS}/*.log") if re.match(r"\d{8}T\d{4}Z-", os.path.basename(p)))
    out = {"running": running, "log": logs[-1] if logs else None}
    if logs:
        name = os.path.basename(logs[-1])
        m = re.match(r"(\d{8}T\d{4})Z-(\w+)\.log", name)
        if m:
            out["started"] = datetime.strptime(m.group(1), "%Y%m%dT%H%M").replace(tzinfo=timezone.utc).timestamp()
            # Computing since GFS was in hand (its log's WPS line; the start waits for NOAA): the expected end from it
            # (the owner, 22.10.2026: progress and the expected finish).
            try:
                out["computing_from"] = wrf_gfs(out["started"])[0]
                out["phases"] = wrf_phases(out["started"])
            except Exception:
                pass
            out["region"] = m.group(2)
        text = read(logs[-1], "") or ""
        box = re.search(r"1 km box from (\S+) to (\S+)", text)
        # The model's own time: WRF's first process log (rsl.out.0000 in the region's run folder) while it runs.
        rsl = f"/root/wrf-work/{out.get('region', 'tarifa')}/wrf/rsl.out.0000"
        tail = ""
        if running and os.path.exists(rsl):
            with open(rsl, "rb") as f:
                f.seek(max(os.path.getsize(rsl) - 4000, 0))
                tail = f.read().decode(errors="replace")
        last = re.findall(r"time (\d{4}-\d\d-\d\d_\d\d:\d\d:\d\d) on domain\s+(\d)", tail or text[-20000:])
        if box and last:
            a, b = (datetime.strptime(x, "%Y-%m-%d_%H:%M:%S") for x in box.groups())
            t = datetime.strptime(last[-1][0], "%Y-%m-%d_%H:%M:%S")
            out["box"] = [a.strftime("%d %H:%M"), b.strftime("%d %H:%M")]
            out["progress"] = max(0.0, min(1.0, (t - a) / (b - a)))
            out["model_time"] = t.strftime("%d %H:%M")
        lines = [line for line in text.splitlines() if line.strip()][-1:] or [""]
        out["last_line"] = lines[0][-200:]
        took = re.search(r"the run took (\d+) minutes", text)
        if took:
            out["took_min"] = int(took.group(1))
        # Started by its task but still waiting for NOAA's GFS cycle (gfs.py --wait, before WRF's lock; the owner,
        # 22.10.2026: "no new run scheduled for WRF at 13:15 as before": it showed neither running nor next).
        out["waiting"] = (not running and "not out yet: waiting" in lines[0] and "the run took" not in text
                          and "the run failed" not in text and time.time() - os.path.getmtime(logs[-1]) < 600)
        out["ended"] = None if running or out["waiting"] else os.path.getmtime(logs[-1])
    return out


def wrf_runs(since):
    """WRF's runs since `since` from its logs: [(start, end, ok)]."""
    out = []
    logs = sorted(p for p in glob.glob(f"{WRF_LOGS}/*.log") if re.match(r"\d{8}T\d{4}Z-", os.path.basename(p)))[-12:]
    for path in logs:
        m = re.match(r"(\d{8}T\d{4})Z-", os.path.basename(path))
        if not m:
            continue
        start = datetime.strptime(m.group(1), "%Y%m%dT%H%M").replace(tzinfo=timezone.utc).timestamp()
        end = os.path.getmtime(path)
        text = read(path, "") or ""
        # Since 23.10.2026 the Mac makes the runs and tower.sh stands aside (wrf_state.py): a log in which the Tower never
        # started WRF itself ("WRF tarifa, 48 hours, on 20 cores") is no run of the Tower's; nor the newest while the Mac
        # is on it (the Tower's own GFS wait before it asks).
        if not re.search(r"^\d\d:\d\d:\d\d WRF \w+, \d+ hours", text, re.M) and (
                "the Mac (" in text or (path == logs[-1] and (mac_state() or {}).get("wrf"))):
            continue
        # The run going now: its log stops changing a few minutes in (the model's progress goes to rsl.out.*); or
        # still waiting for GFS (wrf_state's "waiting").
        state = wrf_state() if path == logs[-1] else {}
        going = path == logs[-1] and state.get("running") and "the run took" not in text and "the run failed" not in text
        waiting = path == logs[-1] and state.get("waiting")
        if going or waiting:
            end = time.time()
        if end >= since:
            out.append((start, end, "going" if going else "waiting" if waiting else "the run failed" not in text))
    return out


def wrf_gfs(start):
    """From the WRF run's log: when GFS was in hand (the WPS line), whether it had to wait for NOAA ("not out yet":
    then that moment is when GFS was out; else GFS was out before WRF started), and the GFS cycle (unix)."""
    paths = glob.glob(f"{WRF_LOGS}/{datetime.fromtimestamp(start, timezone.utc):%Y%m%dT%H%M}Z-*.log")
    text = (read(paths[0], "") or "") if paths else ""
    ready = cycle = None
    m = re.search(r"^(\d\d):(\d\d):(\d\d) WPS", text, re.M)
    if m:
        ready = start - start % 86400 + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if ready < start - 60:
            ready += 86400
    c = re.search(r"^GFS (\d{4}-\d\d-\d\d) (\d\d)Z", text, re.M)
    if c:
        cycle = int(datetime.strptime(f"{c[1]} {c[2]}", "%Y-%m-%d %H").replace(tzinfo=timezone.utc).timestamp())
    else:
        # Still waiting ("GFS 22 06Z not out yet: waiting"): the cycle it waits for, in the start's month.
        w = re.search(r"^GFS (\d\d) (\d\d)Z not out yet", text, re.M)
        if w:
            day = datetime.fromtimestamp(start, timezone.utc)
            try:
                cycle = int(day.replace(day=int(w[1]), hour=int(w[2]), minute=0, second=0, microsecond=0).timestamp())
            except ValueError:
                cycle = None
    return ready, "not out yet" in text, cycle


def wrf_phases(start):
    """WRF's steps from its run's log (the owner, 22.10.2026: the popup's timeline): GFS out at NOAA, GFS in hand
    (its MB and seconds), WPS, the model's start, its end; times (unix) as found."""
    paths = glob.glob(f"{WRF_LOGS}/{datetime.fromtimestamp(start, timezone.utc):%Y%m%dT%H%M}Z-*.log")
    text = (read(paths[0], "") or "") if paths else ""

    def at(pattern):
        m = re.search(pattern, text, re.M)
        if not m:
            return None
        h, mi, se = (int(v) for v in m.group(1).split(":"))
        t = start - start % 86400 + h * 3600 + mi * 60 + se
        return t + 86400 if t < start - 60 else t
    out = {"gfs_out": at(r"^(\d\d:\d\d:\d\d) GFS .*out at NOAA"), "gfs_done": at(r"^(\d\d:\d\d:\d\d) GFS in hand"),
           "wps": at(r"^(\d\d:\d\d:\d\d) WPS"), "model": at(r"^\[(\d\d:\d\d:\d\d)\] \d+ cores"), "end": at(r"^(\d\d:\d\d:\d\d) the run took")}
    size = re.search(r"GFS in hand: (\d+) MB in (\d+) s", text)
    if size:
        out["gfs_mb"], out["gfs_s"] = int(size[1]), int(size[2])
    return {k: round(v) for k, v in out.items() if v is not None}


def wrf_next(now, count=3):
    """The next WRF starts (unix), from the Tower's tasks in Riga time."""
    out = []
    day = datetime.fromtimestamp(now, RIGA).date()
    for d in range(3):
        for h, m in WRF_AT:
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=RIGA) + timedelta(days=d)
            if t.timestamp() > now:
                out.append(t.timestamp())
    return sorted(out)[:count]


# MARK: Records: runs, typical times, GitHub

class Records:
    def __init__(self):
        self.lock = threading.Lock()
        self.github = {}        # job -> median minutes
        self.github_at = 0
        self.slow = {}
        self.slow_at = 0

    def typical(self, job):
        """The job's usual minutes (median of its last 10 good runs here), None before any."""
        walls = [r["wall_s"] for r in jsonl(f"{STATS}/{job}.jsonl", 20) if r.get("exit") == 0 and r.get("wall_s")][-10:]
        return round(statistics.median(walls) / 60, 1) if walls else None

    def fetch_github(self):
        out = {}
        for wf in sorted({w for w, _ in GITHUB_OF.values()}):
            try:
                request = urllib.request.Request(f"{GITHUB}{wf}/runs?status=success&per_page=40", headers=AGENT)
                with urllib.request.urlopen(request, timeout=30) as reply:
                    runs = json.load(reply).get("workflow_runs", [])
            except Exception as error:
                log(f"github {wf}: {error}")
                continue
            for job, (w, word) in GITHUB_OF.items():
                if w != wf:
                    continue
                minutes = []
                for r in runs:
                    if word and (r.get("display_title") or "").split()[-1:] != [word]:
                        continue
                    try:
                        a = datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00"))
                        b = datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
                    except (KeyError, ValueError, AttributeError):
                        continue
                    m = (b - a).total_seconds() / 60
                    if m > 1.5:  # not the runs that found nothing new
                        minutes.append(m)
                if minutes:
                    out[job] = round(statistics.median(minutes[:10]), 1)
        with self.lock:
            self.github, self.github_at = out, time.time()
        try:
            with open(f"{STATS}/_github_medians.json", "w") as f:
                json.dump({"at": self.github_at, "github": out}, f)
        except OSError:
            pass

    def github_loop(self):
        # The medians kept across the server's reloads: 22 calls each time otherwise (GitHub's 60 an hour ran out
        # on 21.10.2026 from the reloads alone).
        try:
            with open(f"{STATS}/_github_medians.json") as f:
                saved = json.load(f)
            with self.lock:
                self.github, self.github_at = saved["github"], saved["at"]
            time.sleep(max(0, saved["at"] + 3 * 3600 - time.time()))
        except (OSError, ValueError, KeyError):
            pass
        while True:
            try:
                self.fetch_github()
            except Exception as error:
                log(f"github: {error}")
            time.sleep(3 * 3600)  # (the public API's 60 calls an hour, shared with the freshness's every 2 minutes)

    def build_slow(self, live, downloads):
        now = time.time()
        since = now - 12 * 3600
        runs = [r for r in jsonl(f"{STATS}/_runs.jsonl", 600) if r.get("end", 0) >= since - 12 * 3600]
        # The jobs' stats lines, for each run's peak memory and download.
        recent = []
        for r in sorted(runs, key=lambda r: -r["end"])[:40]:
            stats = next((s for s in reversed(jsonl(f"{STATS}/{r['job']}.jsonl", 30))
                          if abs(s.get("start", 0) - r["start"]) < 120), {})
            recent.append({"job": r["job"], "start": r["start"], "end": r["end"], "exit": r.get("exit"), "live": r.get("live"),
                           "took": round((r["end"] - r["start"]) / 60, 1), "peak_mb": stats.get("peak_rss_mb"),
                           "net_mb": stats.get("net_in_mb"), "github": self.github.get(r["job"]),
                           "tail": (stats.get("tail") or [])[-1:]})
        items = []
        for r in runs:
            if r["end"] >= since:
                items.append({"lane": lane(r["job"]), "job": r["job"], "a": r["start"], "b": r["end"],
                              "kind": "ran" if r.get("exit") == 0 else "bad"})
        for c in (live or {}).get("running", []):
            if c.get("started"):
                items.append({"lane": lane(c["job"]), "job": c["job"], "a": c["started"], "b": now, "kind": "run"})
        for a, b, ok in wrf_runs(since) + [(r[0], r[1], r[2]) for r in mac_wrf_runs(since)]:
            items.append({"lane": "WRF", "job": "wrf", "a": a, "b": b, "kind": "run" if ok in ("going", "waiting") else "wrf" if ok else "bad"})
        wrf_typ = 3.5 * 3600
        for t in wrf_next(now):
            if t < now + 6 * 3600:
                items.append({"lane": "WRF", "job": "wrf", "a": t, "b": t + wrf_typ, "kind": "wrfplan"})
        for n in (live or {}).get("next", []):
            if n.get("at") and now < n["at"] < now + 6 * 3600:
                typ = (self.typical(n["job"]) or 5) * 60
                items.append({"lane": lane(n["job"]), "job": n["job"], "a": n["at"], "b": n["at"] + typ, "kind": "plan"})
        arr = [{"t": a["done"], "label": f"{a['model']} {datetime.fromtimestamp(a['run'], timezone.utc):%H}Z"}
               for a in jsonl(f"{STATS}/_arrivals.jsonl", 400) if a.get("done", 0) >= since]
        with downloads.lock:
            hosts = {k: dict(v) for k, v in downloads.hosts.items()}
        typical = {}
        for c in (live or {}).get("running", []) + (live or {}).get("waiting", []):
            typical[c["job"]] = self.typical(c["job"])
        # The home runs of the last 36 hours, for the Pipeline's Tower line under GitHub's bars (the owner, 21.10.2026:
        # "those runs according github or tower pc?").
        home = [{"job": r["job"], "due": r.get("due"), "start": r["start"], "end": r["end"], "exit": r.get("exit"), "live": bool(r.get("live")),
                 "source": r.get("source"), "sources": r.get("sources")} for r in jsonl(f"{STATS}/_runs.jsonl", 1500) if r.get("end", 0) >= now - 36 * 3600]
        # And the runs of the Tower's tiny jobs (their own record) and the Mac's fast jobs, each with its machine (the
        # owner, 26.10.2026: "we have to define on live feed what run on tower what on mac mini").
        home += [{"job": r["job"], "start": r["start"], "end": r["end"], "exit": r.get("exit"), "live": bool(r.get("live")),
                  "host": host} for path, host in ((f"{STATS}/_tiny_runs.jsonl", "tower"), (MAC_RUNS, "mac"))
                 for r in jsonl(path, 3000) if r.get("end", 0) >= now - 36 * 3600 and r.get("start")]
        # Each run's cost from its job's stats (the Pipeline's popup, the owner, 22.10.2026): CPU minutes, peak MB, MB in.
        stats = {job: jsonl(f"{STATS}/{job}.jsonl", 120) for job in {r["job"] for r in home}}
        downloads = {}
        for d in jsonl(f"{STATS}/_downloads.jsonl", 3000):
            downloads.setdefault(d["job"], []).append(d)
        for r in home:
            for d in downloads.get(r["job"], []):
                if abs(d["start"] - r["start"]) < 180:
                    r["dl_s"], r["dl_mb"], r["dl_files"], r["dl_first"] = d["busy_s"], d["mb"], d["files"], d.get("first")
                    break
        for r in home:
            for st in stats.get(r["job"], []):
                if abs((st.get("start") or 0) - r["start"]) < 120:
                    r["cpu"] = round(((st.get("cpu_user_s") or 0) + (st.get("cpu_sys_s") or 0)) / 60, 1)
                    r["peak"] = st.get("peak_rss_mb")
                    r["net"] = round(st.get("net_in_mb") or 0)
                    break
        # The Pipeline's timeline (the owner, 21.10.2026: "our time", from a run being available to riders seeing it;
        # mockup docs/mockups/pipeline-timeline-2026-10-21.html): each maker's run and when it became complete on
        # Open-Meteo's open data (the first record of each), GitHub's map jobs (start, end), WRF's runs.
        arrived = {}
        for a in jsonl(f"{STATS}/_arrivals.jsonl", 6000):
            if a.get("done", 0) >= now - 40 * 3600:
                arrived.setdefault((a["model"], a["run"]), a["done"])
        pipe = {"arrivals": [[m, r, d] for (m, r), d in arrived.items()], "gh": views.GH.pipe(),
                # Each WRF run with the machine that made it, the Tower's and the Mac's, by start.
                "wrf": sorted([[round(a), round(b), ok if ok in ("going", "waiting") else bool(ok), *wrf_gfs(a), wrf_phases(a), "tower"]
                               for a, b, ok in wrf_runs(now - 36 * 3600)] + mac_wrf_runs(now - 36 * 3600))}
        self.slow = {"home": home, "pipe": pipe, "t": round(now), "from": round(since), "to": round(now + 6 * 3600), "lanes": [n for n, _ in LANES],
                     "items": items, "arrivals": arr, "runs": recent, "hosts": hosts, "typical": typical,
                     "github_at": round(self.github_at) or None}
        self.slow_at = now
        return self.slow


# MARK: The answers

DOWNLOADS = Downloads()
MACHINE = Machine(DOWNLOADS)
RECORDS = Records()
FRESH = views.Freshness()
ADMIN = views.Admin()
CLIENTS = [0]
_capacity = {"at": 0, "data": {}}


def summaries_loop():
    """Freshness every 2 minutes and the admin view every minute while someone watches (else every 10)."""
    last_fresh = last_admin = 0
    while True:
        now = time.time()
        watched = now - MACHINE.watched < 120
        if now - last_fresh > (120 if watched else 600):
            last_fresh = now
            try:
                FRESH.refresh(wrf_state())
            except Exception as error:
                log(f"freshness: {error}")
        try:
            DOWNLOADS.sync({r["job"]: r.get("started") for r in live_state().get("running", []) if r.get("started")})
        except Exception as error:
            log(f"downloads: {error}")
        # The live feeds' publishes (views.FEEDS: a HEAD each, every 2 minutes).
        try:
            views.FEEDS.refresh()
        except Exception as error:
            log(f"feeds: {error}")
        # (admin's overview reads many database rows: every 5 minutes while watched, else every 30)
        if now - last_admin > (300 if watched else 1800):
            last_admin = now
            try:
                ADMIN.refresh()
            except Exception as error:
                log(f"admin: {error}")
        time.sleep(10)


def capacity():
    if time.time() - _capacity["at"] > 60:
        try:
            _capacity["data"] = views.capacity()
        except Exception as error:
            log(f"capacity: {error}")
        _capacity["at"] = time.time()
    return _capacity["data"]


_band = {"at": 0, "hours": 0, "data": {}}
# The Mac mini (the owner, 23.10.2026: both WRF runs on the Mac, "the Mac's runs and CPU/memory in its state file, the
# panel reading it"): its beat (wrf_poc/mac_beat.py, every 2 minutes: CPU with WRF's apart, memory, the WRF run going,
# its last runs) read at most once a minute; each new beat's numbers kept for the Mac's band.
MAC_URL = "https://files.meteogust.com/runner/mac.json"
MAC_SAMPLES = f"{STATS}/_mac_samples.jsonl"
# The Mac's fast jobs' runs (its beat's newest 40 "fast_runs", the ones not seen yet; 26.10.2026).
MAC_RUNS = f"{STATS}/_mac_runs.jsonl"
_mac_seen_runs = set()
_mac = {"at": 0, "data": None, "lock": threading.Lock()}


def mac_state():
    """The Mac's newest beat ({} keys as mac_beat.py writes them), None before its first."""
    now = time.time()
    with _mac["lock"]:
        if now - _mac["at"] < 60:
            return _mac["data"]
        _mac["at"] = now
        try:
            request = urllib.request.Request(f"{MAC_URL}?t={int(now)}", headers=AGENT)
            with urllib.request.urlopen(request, timeout=10) as reply:
                data = json.loads(reply.read().decode())
        except Exception:
            return _mac["data"]
        if data.get("t") and data.get("t") != (_mac["data"] or {}).get("t"):
            try:
                with open(MAC_SAMPLES, "a") as f:
                    # WRF's own memory and the Mac's network from 25.10.2026 (WRF's run card).
                    f.write(json.dumps({"t": data["t"], "wrf": data.get("cpu_wrf"), "other": data.get("cpu_other"),
                                        "mem": data.get("mem_used"), "mem_wrf": data.get("mem_wrf"),
                                        "net_in": data.get("net_in"), "net_out": data.get("net_out")}) + "\n")
                if not _mac_seen_runs:
                    _mac_seen_runs.update((r.get("job"), r.get("start")) for r in jsonl(MAC_RUNS, 3000))
                new = [r for r in data.get("fast_runs") or [] if (r.get("job"), r.get("start")) not in _mac_seen_runs]
                if new:
                    with open(MAC_RUNS, "a") as f:
                        for r in new:
                            f.write(json.dumps(r) + "\n")
                            _mac_seen_runs.add((r.get("job"), r.get("start")))
                    if os.path.getsize(MAC_RUNS) > 600_000:
                        keep = (read(MAC_RUNS, "") or "").splitlines()[-4000:]
                        with open(MAC_RUNS, "w") as f:
                            f.write("\n".join(keep) + "\n")
                # About 8 days at one a beat, as the scheduler keeps its own samples (a line ~130 bytes).
                if os.path.getsize(MAC_SAMPLES) > 900_000:
                    keep = (read(MAC_SAMPLES, "") or "").splitlines()[-6000:]
                    with open(MAC_SAMPLES, "w") as f:
                        f.write("\n".join(keep) + "\n")
            except OSError:
                pass
        _mac["data"] = data
        return data


def mac_wrf_runs(since):
    """The Mac's WRF runs since `since` as the Pipeline's WRF tuples: [start, end, ok, GFS in hand, waited for it, cycle,
    phases, "mac"] (ok: "going" or "waiting" for the run on now)."""
    m = mac_state() or {}
    out = []
    for r in (m.get("runs") or []) + ([m["wrf"]] if m.get("wrf") else []):
        at = r.get("at") or {}
        start = r.get("started")
        if not start:
            continue
        going = r is m.get("wrf")
        end = time.time() if going else at.get("done") or at.get("failed") or r.get("t") or start
        if end < since:
            continue
        ok = ("waiting" if r.get("stage") == "waiting" else "going") if going else r.get("stage") == "done"
        ready = at.get("wps")
        cycle = None
        c = re.match(r"(\d{4}-\d\d-\d\d)T(\d\d)Z", r.get("cycle") or "")
        if c:
            cycle = int(datetime.strptime(f"{c[1]} {c[2]}", "%Y-%m-%d %H").replace(tzinfo=timezone.utc).timestamp())
        phases = {k: v for k, v in {"gfs_done": ready, "wps": ready, "model": at.get("wrf"), "end": at.get("tiles")}.items() if v}
        out.append([round(start), round(end), ok, ready, bool(ready and ready - start > 300), cycle, phases, "mac"])
    return out


def band(hours=12):
    """The machine band over the Pipeline (the owner, 22.10.2026: "on top we could add a total PC usage graph", to see
    waiting that the machine doesn't cause): every 2 minutes of the last `hours`, [time, CPU of the jobs %, CPU of WRF %
    (of the threads), memory used GB, jobs running, jobs waiting, WRF on 1/0, {why: count}, [waiting jobs' indexes in
    `names`], the jobs' memory budget GB, network in MB/s, out MB/s]. Why from STATS/_queue.jsonl (the scheduler, from 22.10.2026); before it, the
    waits from the runs' due and start, why unknown ("?"). Kept a minute."""
    now = time.time()
    if now - _band["at"] < 60 and _band["hours"] == hours:
        return _band["data"]
    since = now - hours * 3600
    threads = os.cpu_count() or 20
    try:
        with open("/proc/meminfo") as f:
            total = int(f.read().split("MemTotal:")[1].split()[0]) / 1024 / 1024
    except (OSError, IndexError, ValueError):
        total = 0.0
    samples = [x for x in jsonl(f"{STATS}/_samples.jsonl", hours * 60 + 120) if x.get("t", 0) >= since]
    queue = [q for q in jsonl(f"{STATS}/_queue.jsonl", hours * 60 + 120) if q.get("t", 0) >= since]
    first_queue = queue[0]["t"] if queue else now
    waits = [(r["due"], r["start"], r["job"]) for r in jsonl(f"{STATS}/_runs.jsonl", 3000)
             if r.get("start", 0) >= since - 6 * 3600 and r.get("due") and r["start"] - r["due"] > 60]
    names, index = [], {}

    def ix(name):
        if name not in index:
            index[name] = len(names)
            names.append(name)
        return index[name]
    step = 120
    rows, si, qi = [], 0, 0
    for b in range(int(since // step * step), int(now), step):
        jobs = wrf = used = net_in = net_out = 0.0
        running, count, on, net_n = 0, 0, False, 0
        while si < len(samples) and samples[si]["t"] < b + step:
            x = samples[si]
            si += 1
            if x["t"] < b:
                continue
            cj = cw = 0.0
            n = 0
            for c in x.get("containers") or []:
                try:
                    pct = float(str(c.get("cpu") or "0").rstrip("%"))
                except ValueError:
                    pct = 0
                name = c.get("name") or ""
                if name.startswith("mg-"):
                    cj += pct
                    n += 1
                elif name != "meteogust-tunnel":
                    cw += pct
            jobs, wrf = jobs + cj / threads, wrf + cw / threads
            used = max(used, total - (x.get("mem_free_mb") or 0) / 1024)
            if x.get("net_in") is not None:
                net_in, net_out, net_n = net_in + x["net_in"], net_out + (x.get("net_out") or 0), net_n + 1
            running = max(running, n)
            on = on or bool(x.get("wrf"))
            count += 1
        if not count:
            continue
        last = None
        while qi < len(queue) and queue[qi]["t"] < b + step:
            if queue[qi]["t"] >= b:
                last = queue[qi]
            qi += 1
        why, who = {}, []
        if last is not None:
            for job, kind, _priority in last.get("wait") or []:
                why[kind] = why.get(kind, 0) + 1
                who.append(ix(job))
            budget = round((last.get("bud") or 0) / 1000, 1)
        else:
            if b < first_queue:
                for due, start, job in waits:
                    if due <= b < start:
                        why["?"] = why.get("?", 0) + 1
                        who.append(ix(job))
            budget = round(total - 1.8 - (9.0 if on else 0), 1)
        # The machine's network in and out (MB/s, the scheduler's samples from 22.10.2026; None before).
        rows.append([b, round(jobs / count, 1), round(wrf / count, 1), round(used, 1), running, len(who), 1 if on else 0,
                     why, who, budget, round(net_in / net_n, 1) if net_n else None, round(net_out / net_n, 1) if net_n else None])
    data = {"t": round(now), "hours": hours, "step": step, "threads": threads, "total_gb": round(total, 1), "names": names, "rows": rows}
    # The Mac's band under the Tower's (the owner, 23.10.2026): [time, CPU of its other work %, CPU of WRF %, memory GB]
    # in the same steps, from its beats (mac_state).
    mac = mac_state()
    beats = [x for x in jsonl(MAC_SAMPLES, hours * 30 + 60) if x.get("t", 0) >= since]
    if mac and beats:
        mrows, i = [], 0
        for b in range(int(since // step * step), int(now), step):
            inside = []
            while i < len(beats) and beats[i]["t"] < b + step:
                if beats[i]["t"] >= b:
                    inside.append(beats[i])
                i += 1
            if inside:
                mrows.append([b, round(sum(x.get("other") or 0 for x in inside) / len(inside), 1),
                              round(sum(x.get("wrf") or 0 for x in inside) / len(inside), 1), max(x.get("mem") or 0 for x in inside)])
        data["mac"] = {"threads": mac.get("cores") or 10, "total_gb": mac.get("mem_total") or 16, "rows": mrows}
    _band.update(at=now, hours=hours, data=data)
    return data


def usage(job, start, due=None):
    """A run's usage samples (runner/measure.py: STATS/_usage/<job>-<start>.json), the one that started nearest `start`
    within 5 minutes (a running job's start here is its container's, a few seconds before measure.py's), {} if none;
    with `due`, "wait": why the scheduler held it from due to start ({reason: its minute records}, STATS/_queue.jsonl;
    the run card's timeline, 23.10.2026). None for a bad job name."""
    if not re.fullmatch(r"[\w.-]+", job or ""):
        return None
    best, out = None, {}
    try:
        for name in os.listdir(f"{STATS}/_usage"):
            m = re.fullmatch(re.escape(job) + r"-(\d+)\.json", name)
            if m and abs(int(m.group(1)) - start) < 300 and (best is None or abs(int(m.group(1)) - start) < best[0]):
                best = (abs(int(m.group(1)) - start), name)
    except OSError:
        pass
    if best:
        try:
            out = json.loads(read(f"{STATS}/_usage/{best[1]}") or "{}")
        except ValueError:
            out = {}
    if due and start - due > 60:
        wait = {}
        # The queue's records are about a minute apart: a day back is enough for the panel's runs.
        for q in jsonl(f"{STATS}/_queue.jsonl", min(1500, int((time.time() - due) / 60) + 30)):
            if due <= q.get("t", 0) <= start:
                for name, kind, _priority in q.get("wait") or []:
                    if name == job:
                        wait[kind] = wait.get(kind, 0) + 1
        out["wait"] = wait
    return out


_DOCKER_UNITS = {"B": 2**-20, "KiB": 2**-10, "kB": 2**-10, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}


def docker_mb(text):
    """docker stats' "4.633GiB / 6.516GiB" -> 4744 (MB used), None when it can't be read (as the scheduler's)."""
    try:
        used = (text or "").split("/")[0].strip()
        number = used.rstrip("BKMGiTkb")
        return float(number) * _DOCKER_UNITS[used[len(number):]]
    except (ValueError, KeyError):
        return None


def wrf_usage(host, start, end=None):
    """WRF's use over its run for its card (the owner, 25.10.2026: "can we show card same as for other tasks same for
    tasks on mac mini"), in measure.py's form ({start, end, step, cpu: % of one thread, mem: MB, net, out: MB/s}, None
    in a step without a sample, a whole series None when there's none): on the Mac its beats every 2 minutes (WRF's
    CPU, its own memory and the Mac's network: those two from 25.10.2026); on the Tower its containers in the
    scheduler's minute samples (the band's rule: not a job's, not the tunnel; no network of their own)."""
    end = end or time.time()
    if host == "mac":
        cores = (mac_state() or {}).get("cores") or 10
        rows = [(x["t"], (x.get("wrf") or 0) * cores, x.get("mem_wrf"), x.get("net_in"), x.get("net_out"))
                for x in jsonl(MAC_SAMPLES, 3000) if start - 60 <= x.get("t", 0) <= end + 60]
        step = 120
    else:
        rows, step = [], 60
        for x in jsonl(f"{STATS}/_samples.jsonl", 3000):
            if not start - 30 <= x.get("t", 0) <= end + 30:
                continue
            cpu = mem = 0.0
            seen = False
            for c in x.get("containers") or []:
                name = c.get("name") or ""
                if name.startswith("mg-") or name == "meteogust-tunnel":
                    continue
                seen = True
                try:
                    cpu += float(str(c.get("cpu") or "0").rstrip("%"))
                except ValueError:
                    pass
                mem += docker_mb(c.get("mem")) or 0
            rows.append((x["t"], cpu if seen else None, mem if seen else None, None, None))
    n = max(1, int((end - start) // step) + 1)
    bins = [[] for _ in range(n)]
    for row in rows:
        i = int((row[0] - start) // step)
        if 0 <= i < n:
            bins[i].append(row)

    def series(k, digits, top=False):
        out = []
        for b in bins:
            v = [r[k] for r in b if r[k] is not None]
            out.append(round(max(v) if top else sum(v) / len(v), digits) if v else None)
        return out if any(v is not None for v in out) else None
    return {"job": "wrf", "host": host, "start": round(start), "end": round(end), "step": step,
            "cpu": series(1, 0), "mem": series(2, 0, True), "net": series(3, 2), "out": series(4, 2)}


def live_state():
    try:
        return json.loads(read(LIVE, "{}") or "{}")
    except ValueError:
        return {}


def fast():
    MACHINE.watched = time.time()
    live = live_state()
    with MACHINE.lock:
        snap = dict(MACHINE.snapshot)
        hist = list(MACHINE.history)
        speeds = {k: list(v) for k, v in MACHINE.job_speed.items()}
    current = DOWNLOADS.job_rates()
    by_name = {c["name"]: c for c in snap.get("containers", [])}
    jobs = []
    for r in live.get("running", []):
        c = by_name.get(r["job"], {})
        d = current.get(r["job"])
        jobs.append({**r, "started": r.get("started") or c.get("started"), "cpu": c.get("cpu"), "mem": c.get("mem"),
                     "net_in": c.get("net_in"), "speeds": speeds.get(r["job"], []), "download": d,
                     "typical": RECORDS.slow.get("typical", {}).get(r["job"]) if RECORDS.slow else None,
                     "dl": DOWNLOADS.summary(r["job"])})
    wrf = wrf_state()
    wc = next((c for c in snap.get("containers", []) if c["kind"] == "wrf"), None)
    if wc:
        wrf.update({"cpu": wc["cpu"], "mem": wc["mem"]})
    wrf["next"] = wrf_next(time.time(), 2)
    # The Mac mini: its beat (a day old at most), the WRF run it's making.
    m = mac_state()
    mac = ({"beat": m.get("t"), "host": m.get("host"), "cores": m.get("cores"), "mem_total": m.get("mem_total"),
            "mem_used": m.get("mem_used"), "cpu_wrf": m.get("cpu_wrf"), "cpu_other": m.get("cpu_other"), "wrf": m.get("wrf")}
           if m and time.time() - (m.get("t") or 0) < 86400 else None)
    return {"t": snap.get("t"), "host": live.get("host") or socket.gethostname(), "mac": mac, "cores": snap.get("cores", []),
            "mem_total": snap.get("mem_total"), "mem_avail": snap.get("mem_avail"),
            "containers": snap.get("containers", []), "net_in": snap.get("net_in"), "net_out": snap.get("net_out"),
            "net_hist": hist, "sources_now": DOWNLOADS.rates(), "jobs": jobs, "wrf": wrf,
            "waiting": live.get("waiting", []), "next": live.get("next", []), "budget": live.get("budget"),
            "reserved": live.get("reserved"), "slots": live.get("slots"), "live": live.get("live", []),
            "beat": live.get("beat"), "live_t": live.get("t"), "clients": CLIENTS[0]}


def slow():
    if time.time() - RECORDS.slow_at > 25:
        RECORDS.build_slow(live_state(), DOWNLOADS)
    return RECORDS.slow


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def allowed(self):
        # Through Cloudflare (it adds Cf-Connecting-Ip) only with Access's sign-in in front (its JWT header).
        if self.headers.get("Cf-Connecting-Ip") and not self.headers.get("Cf-Access-Jwt-Assertion"):
            self.reply(403, "text/plain", b"This panel opens only behind Cloudflare Access (its sign-in isn't set up "
                                         b"for this address yet).\n")
            return False
        return True

    def reply(self, code, kind, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True

    def send_event(self, name, data):
        self.wfile.write(f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode())
        self.wfile.flush()

    def do_GET(self):
        if not self.allowed():
            return
        url = urllib.parse.urlsplit(self.path)
        try:
            if url.path in ("/", "/index.html"):
                self.reply(200, "text/html; charset=utf-8", (read(f"{HERE}/index.html", "") or "").encode())
            elif url.path in ("/admin", "/admin/"):
                self.reply(*adminproxy.page())
            elif url.path == "/admin/check":
                self.reply(*adminproxy.check_page())
            elif url.path.startswith("/admin/api/"):
                self.reply(*adminproxy.pass_get(url.path[len("/admin/api/"):], url.query))
            elif url.path == "/api/fast":
                self.reply(200, "application/json", json.dumps(fast()).encode())
            elif url.path == "/api/slow":
                self.reply(200, "application/json", json.dumps(slow()).encode())
            elif url.path == "/api/fresh":
                self.reply(200, "application/json", json.dumps(FRESH.data).encode())
            elif url.path == "/api/capacity":
                self.reply(200, "application/json", json.dumps(capacity()).encode())
            elif url.path == "/api/band":
                hours = urllib.parse.parse_qs(url.query).get("hours", ["12"])[0]
                self.reply(200, "application/json", json.dumps(band(int(hours) if hours in ("6", "12", "24") else 12),
                                                               separators=(",", ":")).encode())
            elif url.path == "/api/admin":
                self.reply(200, "application/json", json.dumps(ADMIN.data).encode())
            elif url.path == "/api/stream":
                self.stream()
            elif url.path == "/api/usage":
                q = urllib.parse.parse_qs(url.query)
                try:
                    due, end = q.get("due", [""])[0], q.get("end", [""])[0]
                    if q.get("job", [""])[0] == "wrf":
                        body = wrf_usage(q.get("host", ["tower"])[0], float(q.get("start", ["0"])[0]), float(end) if end else None)
                    else:
                        body = usage(q.get("job", [""])[0], float(q.get("start", ["0"])[0]), float(due) if due else None)
                except ValueError:
                    body = None
                self.reply(200, "application/json", json.dumps(body, separators=(",", ":")).encode())
            elif url.path == "/api/log":
                self.log_stream(urllib.parse.parse_qs(url.query).get("job", [""])[0])
            else:
                self.reply(404, "text/plain", b"Not here\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if not self.allowed():
            return
        url = urllib.parse.urlsplit(self.path)
        try:
            if not url.path.startswith("/admin/api/"):
                self.reply(404, "text/plain", b"Not here")
            elif not adminproxy.allowed_post(self):
                self.reply(403, "application/json", b'{"error": "Changes only from the control panel page"}')
            else:
                # Up to admin's own limit, a spot video's 40 MB (made small in the editor, ~19 MB); bigger is refused
                # here, not passed on empty.
                length = int(self.headers.get("Content-Length") or 0)
                if length > adminproxy.MAX_BODY:
                    # Read and dropped first: answered with the body unread, the request hung in Cloudflare's tunnel
                    # (a 43 MB test, 23.10.2026) instead of showing this.
                    left = length
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        left -= len(chunk)
                    self.close_connection = True
                    self.reply(413, "application/json", json.dumps({"error": f"Too big for the panel ({length // 1_000_000} MB, at most 41)"}).encode(),
                               {"Connection": "close"})
                    return
                body = self.rfile.read(length) if length > 0 else b""
                self.reply(*adminproxy.pass_post(url.path[len("/admin/api/"):], url.query, body, self.headers.get("Content-Type")))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def stream(self):
        self.events()
        CLIENTS[0] += 1
        try:
            last_slow = 0
            while True:
                if time.time() - last_slow > 30:
                    self.send_event("slow", slow())
                    self.send_event("fresh", FRESH.data)
                    self.send_event("capacity", capacity())
                    self.send_event("admin", ADMIN.data)
                    last_slow = time.time()
                self.send_event("fast", fast())
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            CLIENTS[0] -= 1

    def log_stream(self, job):
        """A job's log, live: its last 40 lines, then each new one (WRF's: its newest log)."""
        if job == "wrf":
            path = wrf_state().get("log")
        else:
            entry = next((r for r in live_state().get("running", []) if r["job"] == job), None)
            path = entry and entry.get("log")
            if not path and re.fullmatch(r"[a-z0-9-]{1,40}", job or ""):
                found = sorted(glob.glob(f"{LOGS}/{job}-2*.log"))
                path = found[-1] if found else None
        self.events()
        if not path or not os.path.exists(path) or not os.path.realpath(path).startswith((LOGS, WRF_LOGS)):
            self.send_event("line", {"text": "(no log for this job)"})
            return
        try:
            with open(path, errors="replace") as f:
                lines = f.readlines()[-40:]
                for line in lines:
                    self.send_event("line", {"text": line.rstrip()[-400:]})
                quiet = 0
                while True:
                    line = f.readline()
                    if line:
                        self.send_event("line", {"text": line.rstrip()[-400:]})
                        quiet = 0
                    else:
                        time.sleep(1)
                        quiet += 1
                        if quiet % 15 == 0:
                            self.wfile.write(b": still here\n\n")
                            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def reload_when_changed():
    files = [__file__, views.__file__, adminproxy.__file__]
    stamp = [os.path.getmtime(f) for f in files]
    while True:
        time.sleep(20)
        try:
            if [os.path.getmtime(f) for f in files] != stamp:
                log("the panel's code changed: reloading")
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        except OSError:
            pass


def main():
    for target in (DOWNLOADS.listen, MACHINE.run, RECORDS.github_loop, summaries_loop, reload_when_changed):
        threading.Thread(target=target, daemon=True).start()
    log(f"ops panel on 127.0.0.1:{PORT}, downloads on UDP {UDP}")
    Server(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
