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
30 s), /api/log?job=<name> (a job's log, live). Listens on 127.0.0.1 only: from outside through the Cloudflare
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

HOME = os.environ.get("MG_HOME", "/var/lib/meteogust")
PORT = int(os.environ.get("MG_OPS_PORT", "8090"))
UDP = int(os.environ.get("MG_OPS_UDP", "8091"))
STATS, LOGS, LIVE = f"{HOME}/stats", f"{HOME}/logs", f"{HOME}/live.json"
WRF_LOGS, WRF_LOCK = "/root/wrf-logs", "/root/wrf-logs/.lock"
HERE = os.path.dirname(os.path.abspath(__file__))
RIGA = ZoneInfo("Europe/Riga")
# The Tower's WRF tasks (Riga time; moved out of the 00Z/12Z peaks on 21.10.2026).
WRF_AT = [(1, 15), (13, 15)]
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
        out["ended"] = None if running else os.path.getmtime(logs[-1])
    return out


def wrf_runs(since):
    """WRF's runs since `since` from its logs: [(start, end, ok)]."""
    out = []
    for path in sorted(p for p in glob.glob(f"{WRF_LOGS}/*.log") if re.match(r"\d{8}T\d{4}Z-", os.path.basename(p)))[-12:]:
        m = re.match(r"(\d{8}T\d{4})Z-", os.path.basename(path))
        if not m:
            continue
        start = datetime.strptime(m.group(1), "%Y%m%dT%H%M").replace(tzinfo=timezone.utc).timestamp()
        end = os.path.getmtime(path)
        if end >= since:
            text = read(path, "") or ""
            out.append((start, end, "the run failed" not in text))
    return out


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

    def github_loop(self):
        while True:
            try:
                self.fetch_github()
            except Exception as error:
                log(f"github: {error}")
            time.sleep(3600)

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
        for a, b, ok in wrf_runs(since):
            items.append({"lane": "WRF", "job": "wrf", "a": a, "b": b, "kind": "wrf" if ok else "bad"})
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
        self.slow = {"t": round(now), "from": round(since), "to": round(now + 6 * 3600), "lanes": [n for n, _ in LANES],
                     "items": items, "arrivals": arr, "runs": recent, "hosts": hosts, "typical": typical,
                     "github_at": round(self.github_at) or None}
        self.slow_at = now
        return self.slow


# MARK: The answers

DOWNLOADS = Downloads()
MACHINE = Machine(DOWNLOADS)
RECORDS = Records()
CLIENTS = [0]


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
                     "typical": RECORDS.slow.get("typical", {}).get(r["job"]) if RECORDS.slow else None})
    wrf = wrf_state()
    wc = next((c for c in snap.get("containers", []) if c["kind"] == "wrf"), None)
    if wc:
        wrf.update({"cpu": wc["cpu"], "mem": wc["mem"]})
    wrf["next"] = wrf_next(time.time(), 2)
    return {"t": snap.get("t"), "host": live.get("host") or socket.gethostname(), "cores": snap.get("cores", []),
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
            elif url.path == "/api/fast":
                self.reply(200, "application/json", json.dumps(fast()).encode())
            elif url.path == "/api/slow":
                self.reply(200, "application/json", json.dumps(slow()).encode())
            elif url.path == "/api/stream":
                self.stream()
            elif url.path == "/api/log":
                self.log_stream(urllib.parse.parse_qs(url.query).get("job", [""])[0])
            else:
                self.reply(404, "text/plain", b"Not here\n")
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
    stamp = os.path.getmtime(__file__)
    while True:
        time.sleep(20)
        try:
            if os.path.getmtime(__file__) != stamp:
                log("server.py changed: reloading")
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        except OSError:
            pass


def main():
    for target in (DOWNLOADS.listen, MACHINE.run, RECORDS.github_loop, reload_when_changed):
        threading.Thread(target=target, daemon=True).start()
    log(f"ops panel on 127.0.0.1:{PORT}, downloads on UDP {UDP}")
    Server(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
