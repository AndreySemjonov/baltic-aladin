"""The helper's own scheduler (the owner, 21.10.2026: production schedules off GitHub; the Tower now, the
Mac mini from the end of the week; GitHub only for the code, tests and deploys). Every minute: each job in
runner/jobs.py that's due (its source has a newer complete run, or its interval passed) starts in its own
container (runner/Dockerfile; measured by runner/measure.py), at most MAX_TOTAL at once and MAX_HEAVY big
ones (one while WRF runs on this machine), in the jobs' priority order (jobs.py; a priority 3 only while
a slot stays free), and only while the memory they'll need fits (each job's peak from its own last runs; the
owner's Tower, 21.10.2026: the regional wind (8.4 GB) and the Mix (8.5 GB) side by side in 15.5 GB, one killed;
the Mac mini has 16). A job a bigger one is waiting for memory doesn't jump ahead of it from a lower priority.
The repo is pulled every 10 minutes (the code is mounted, not built in).

Shadow mode (the default): every job writes to --local under the work folder, removed after it's measured,
nothing uploaded; GitHub stays live. A job named in LIVE_FILE runs live instead (uploads with INGEST_TOKEN
from ENV_FILE): the switch, one job at a time, its GitHub schedule turned off then.

While WRF runs here (its lock), the jobs' containers get a low CPU share (LOW_SHARES of Docker's 1024; the
owner, 21.10.2026: WRF keeps the fast cores, the jobs use what's left: the slow cores and the fast ones'
second threads), set again on the running ones as WRF starts and ends. The scheduler reloads itself when its
code changes (after a pull); a job whose container is still there (mg-<job>, e.g. from before a reload)
counts as running.

The failover (the owner, 21.10.2026): every 5 minutes a heartbeat to our file store (files.meteogust.com/runner/
heartbeat.json, with INGEST_TOKEN from ENV_FILE): the jobs it runs live, less those whose last two runs failed.
helper-trigger leaves GitHub's runs of those jobs out while the heartbeat is fresh, and starts them again when it
is 20 minutes old or a job leaves the list; station-history tells the admins' phones when it stops.

The ops panel (runner/ops/server.py; the owner, 21.10.2026: live, read-only, ops.meteogust.com through a
Cloudflare Tunnel behind Access): every minute LIVE (live.json) says what runs, what waits and why, and what's
next and when; each model's new runs go in STATS/_arrivals.jsonl (when they came, for the expected times). The
scheduler starts the panel's server when it isn't running, and the tunnel's container (cloudflared) when
CLOUDFLARE_TUNNEL_TOKEN is in ENV_FILE. The jobs' containers get MG_OPS (the panel's address for their downloads,
runner/ops/hook/sitecustomize.py).

Written next to it: the jobs' measurements (STATS/<job>.jsonl), the scheduler's own record of each run
(STATS/_runs.jsonl: when due, started, ended, live or not) and every minute each container's CPU and
memory and the machine's load and free memory (STATS/_samples.jsonl); each run's log in LOGS.

    python3 runner/scheduler.py            (runner/run.sh starts it, and builds the image first)
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jobs import JOBS  # noqa: E402

REPO = os.environ.get("MG_REPO", "/root/baltic-aladin")
HOME = os.environ.get("MG_HOME", "/var/lib/meteogust")
WORK = os.environ.get("MG_WORK", "/mnt/d/meteogust/runner-work")
STATS, LOGS, STATE, LIVE = f"{HOME}/stats", f"{HOME}/logs", f"{HOME}/state.json", f"{HOME}/live.json"
ENV_FILE = os.environ.get("MG_ENV", "/root/.meteogust.env")
LIVE_FILE = os.environ.get("MG_LIVE", "/root/.meteogust-live")
IMAGE = os.environ.get("MG_IMAGE", "meteogust-helper")
MAX_TOTAL, MAX_HEAVY = int(os.environ.get("MG_MAX", "4")), int(os.environ.get("MG_MAX_HEAVY", "2"))
WRF_LOCK = "/root/wrf-logs/.lock"
REGION = os.environ.get("MG_REGION", "eu")
LOW_SHARES = int(os.environ.get("MG_LOW_SHARES", "128"))
# The jobs' memory: all but this much of the machine's (MB), unless MG_MEM_MB says.
MEM_SPARE = 1800
MEM_DEFAULT = {True: 6000, False: 1500}
SMALL_MB = 1500
# WRF's own memory while it runs here (the Tower's 1 km Tarifa run: 8.4 GB), not counted in the jobs' peaks.
WRF_MB = int(os.environ.get("MG_WRF_MB", "9000"))
HERE = os.path.dirname(os.path.abspath(__file__))
BUCKET = "https://openmeteo.s3.amazonaws.com/data_spatial/"
HEARTBEAT = "https://data.meteogust.com/tiles/upload/runner/heartbeat.json"
# The ops panel: its server (127.0.0.1:OPS_PORT, the tunnel's origin) and the jobs' download reports (UDP to the
# Docker bridge's host address); the tunnel's container.
OPS_PORT, OPS_UDP = 8090, 8091
OPS_HOST = os.environ.get("MG_OPS_HOST", "172.17.0.1")
TUNNEL = "meteogust-tunnel"
# A version two weeks old or more (Cloudflare marks old connectors "Unsupported": 2024.12.2 was, 21.10.2026).
TUNNEL_IMAGE = "cloudflare/cloudflared:2026.10.0"
AGENT = {"User-Agent": "MeteoGust helper runner (data.meteogust.com)"}


def log(text):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {text}", flush=True)


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, value):
    with open(path + ".tmp", "w") as f:
        json.dump(value, f)
    os.replace(path + ".tmp", path)


def append(path, record):
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def env_names():
    """The variables set in ENV_FILE (names only)."""
    names = set()
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    names.add(line.split("=", 1)[0].strip())
    except OSError:
        pass
    return names


def env_value(name):
    """One variable's value from ENV_FILE (None when missing); never printed."""
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if line.split("=", 1)[0].strip() == name and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def heartbeat(state, live, running, wrf):
    """The runner's heartbeat (see the top); False when it couldn't be sent."""
    token = env_value("INGEST_TOKEN")
    if not token:
        return False
    failing = sorted(name for name in live if state.get(name, {}).get("fails", 0) >= 2)
    body = json.dumps({"t": round(time.time()), "host": os.uname().nodename, "live": sorted(set(live) - set(failing)),
                       "failing": failing, "running": sorted(running), "wrf": wrf}).encode()
    try:
        request = urllib.request.Request(HEARTBEAT, data=body, method="PUT",
                                         headers={"Authorization": "Bearer " + token, **AGENT})
        with urllib.request.urlopen(request, timeout=30):
            return True
    except Exception as error:
        log(f"heartbeat not sent ({error})")
        return False


def live_jobs():
    try:
        with open(LIVE_FILE) as f:
            return {line.strip() for line in f if line.strip() and not line.startswith("#")}
    except OSError:
        return set()


def wrf_running():
    """WRF's own lock (wrf_poc/tower.sh holds it while a run goes)."""
    if not os.path.exists(WRF_LOCK):
        return False
    return subprocess.run(["flock", "-n", WRF_LOCK, "true"]).returncode != 0


_runs_seen, _arrived = {}, set()
# When each due job first became due and couldn't start (the ops panel's Capacity: from due to started is the
# wait, where the machine runs short); STATS/_runs.jsonl's "due".
_due_since = {}


def newest_run(model, cache):
    """A model's newest complete run on Open-Meteo's open data (unix seconds), None when unknown; asked at most
    every 2 minutes. A run not seen before goes in STATS/_arrivals.jsonl (when it came: the panel's expected times)."""
    if model not in cache and model in _runs_seen and time.time() - _runs_seen[model][0] < 120:
        cache[model] = _runs_seen[model][1]
    if model not in cache:
        try:
            request = urllib.request.Request(f"{BUCKET}{model}/latest.json", headers=AGENT)
            with urllib.request.urlopen(request, timeout=20) as reply:
                latest = json.load(reply)
            run = datetime.fromisoformat(latest["reference_time"].replace("Z", "+00:00")).timestamp() if latest.get("completed") else None
            done = datetime.fromisoformat(latest["last_modified_time"].replace("Z", "+00:00")).timestamp()
            cache[model] = (run, done)
            _runs_seen[model] = (time.time(), (run, done))
            if run and (model, round(run)) not in _arrived:
                # latest.json's last change is when the run became complete, whenever we look.
                append(f"{STATS}/_arrivals.jsonl", {"model": model, "run": round(run), "done": round(done or time.time()),
                                                   "seen": round(time.time())})
                _arrived.add((model, round(run)))
        except Exception:
            cache[model] = (None, None)
    return cache[model]


def arrivals():
    """{model: [seconds from each run's start to it being complete]}, the last 8 of each."""
    out = {}
    try:
        with open(f"{STATS}/_arrivals.jsonl") as f:
            for line in f.readlines()[-2000:]:
                a = json.loads(line)
                out.setdefault(a["model"], []).append(a["done"] - a["run"])
    except (OSError, ValueError, KeyError):
        pass
    return {m: v[-8:] for m, v in out.items()}


def expected(job, state, cache, now, lags):
    """When a job not due now should be next (unix), and why, for the panel."""
    mine = state.get(job.name, {})
    if job.watch:
        best = None
        for model, every in job.watch:
            run, done = newest_run(model, cache)
            if not run:
                continue
            nxt = run + every * 3600
            lag = sorted(lags.get(model) or [0])[len(lags.get(model) or [0]) // 2]
            at = nxt + lag + job.delay * 3600
            why = f"{model} {datetime.fromtimestamp(nxt, timezone.utc):%H}Z" + (
                f", usually {lag / 3600:.1f} h after the run" if lag else "")
            if best is None or at < best[0]:
                best = (at, why)
        return best or (None, "its source's run isn't known yet")
    if job.every:
        return mine.get("started", now) + job.every * 60, f"every {job.every} min"
    return None, ""


def ops_up():
    """Whether the panel's server answers on its port."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", OPS_PORT), timeout=1):
            return True
    except OSError:
        return False


def keep_ops():
    """The panel's server started when it's down (its own log in LOGS/ops.log); the tunnel's container when its
    token is set (cloudflared reads TUNNEL_TOKEN; the value never printed)."""
    if not ops_up():
        server = f"{HERE}/ops/server.py"
        if os.path.exists(server):
            with open(f"{LOGS}/ops.log", "a") as out:
                subprocess.Popen([sys.executable, server], stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                                 env={**os.environ, "MG_HOME": HOME, "MG_OPS_PORT": str(OPS_PORT), "MG_OPS_UDP": str(OPS_UDP)})
            log("ops panel: started")
    token = env_value("CLOUDFLARE_TUNNEL_TOKEN")
    if token:
        there = subprocess.run(["docker", "ps", "-q", "-f", f"name=^{TUNNEL}$"], capture_output=True, text=True).stdout.strip()
        image = subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}", TUNNEL], capture_output=True, text=True).stdout.strip()
        if not there or image != TUNNEL_IMAGE:
            subprocess.run(["docker", "rm", "-f", TUNNEL], capture_output=True)
            started = subprocess.run(["docker", "run", "-d", "--name", TUNNEL, "--restart", "unless-stopped", "--network", "host",
                                      "-e", "TUNNEL_TOKEN", TUNNEL_IMAGE, "tunnel", "--no-autoupdate", "run"],
                                     capture_output=True, text=True, env={**os.environ, "TUNNEL_TOKEN": token})
            log("ops tunnel: started" if started.returncode == 0 else f"ops tunnel: not started ({started.stderr.strip()[:200]})")


def due(job, state, cache, now):
    """The run (or moment) a job is due for, None when it isn't."""
    mine = state.get(job.name, {})
    if job.watch:
        best = None
        for model, every in job.watch:
            run, done = newest_run(model, cache)
            if run is None or datetime.fromtimestamp(run, timezone.utc).hour % every:
                continue
            if done and now - done < job.delay * 3600:
                continue
            if run > mine.get("source", 0) and (best is None or run > best):
                best = run
        return best
    if job.every and now - mine.get("started", 0) >= job.every * 60:
        return now
    return None


def mem_budget():
    if os.environ.get("MG_MEM_MB"):
        return int(os.environ["MG_MEM_MB"])
    with open("/proc/meminfo") as f:
        total = next(int(line.split()[1]) for line in f if line.startswith("MemTotal"))
    return total // 1024 - MEM_SPARE


def mem_need(job, _cache={}):
    """A job's memory (MB): the most of its last 10 runs' peaks and a margin, else a default; read every 10 minutes."""
    now = time.time()
    if job.name in _cache and now - _cache[job.name][0] < 600:
        return _cache[job.name][1]
    peaks = []
    try:
        with open(f"{STATS}/{job.name}.jsonl") as f:
            for line in f.readlines()[-10:]:
                peaks.append(json.loads(line).get("peak_rss_mb") or 0)
    except (OSError, ValueError):
        pass
    need = int(max(peaks) * 1.15) if peaks and max(peaks) > 0 else MEM_DEFAULT[job.heavy]
    _cache[job.name] = (now, need)
    return need


_ids = {}


def used_mb(name):
    """A running job's memory now (MB, its container's resident memory; 0 when unknown)."""
    try:
        if name not in _ids:
            _ids[name] = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", f"mg-{name}"], capture_output=True,
                                        text=True, timeout=10).stdout.strip()
        cid = _ids[name]
        for path, key in ((f"/sys/fs/cgroup/memory/docker/{cid}/memory.stat", "total_rss"),
                          (f"/sys/fs/cgroup/docker/{cid}/memory.stat", "anon")):
            if os.path.exists(path):
                with open(path) as f:
                    for line in f:
                        if line.startswith(key + " "):
                            return int(line.split()[1]) // 2**20
    except Exception:
        pass
    return 0


def code_stamp():
    """The runner's own code, to reload when a pull changed it."""
    stamp = []
    for name in sorted(os.listdir(HERE)):
        if name.endswith(".py"):
            stamp.append((name, os.path.getmtime(f"{HERE}/{name}")))
    return stamp


def containers():
    """The jobs' containers there now (mg-<job>)."""
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
    return {name[3:] for name in out.split() if name.startswith("mg-")}


def set_shares(names, shares):
    for name in names:
        subprocess.run(["docker", "update", "--cpu-shares", str(shares), f"mg-{name}"], capture_output=True)


class Running:
    def __init__(self, job, live, source, wanted, shares=1024):
        self.job, self.live, self.source, self.wanted = job, live, source, wanted
        self.started = time.time()
        self.name = f"mg-{job.name}"
        folder = f"{WORK}/{job.name}"
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
        # MG_REGION: the European mirrors and partial reads (sources.py, omremote.py; 21.10.2026).
        command = ["docker", "run", "--rm", "--name", self.name, "--cpu-shares", str(shares), "-e", f"MG_JOB={job.name}",
                   "-e", f"MG_REGION={REGION}", "-e", f"MG_OPS={OPS_HOST}:{OPS_UDP}",
                   "-e", "PYTHONPATH=/code/runner/ops/hook",
                   "-v", f"{REPO}:/code", "-v", f"{folder}:/work", "-v", f"{STATS}:/stats"]
        if os.path.exists(ENV_FILE):
            command += ["--env-file", ENV_FILE]
        script = ["python", *job.args] + ([] if live else ["--local", "/work/out"])
        command += [IMAGE, "python", "/measure.py", job.name, "--", *script]
        self.log_path = f"{LOGS}/{job.name}-{datetime.now(timezone.utc):%Y%m%dT%H%M}.log"
        self.log_file = open(self.log_path, "w")
        self.process = subprocess.Popen(command, stdout=self.log_file, stderr=subprocess.STDOUT)

    def finished(self):
        code = self.process.poll()
        if code is None and time.time() - self.started > self.job.timeout * 60:
            log(f"{self.job.name}: over {self.job.timeout} min, stopped")
            subprocess.run(["docker", "kill", self.name], capture_output=True)
            code = self.process.wait()
        if code is None:
            return None
        self.log_file.close()
        shutil.rmtree(f"{WORK}/{self.job.name}", ignore_errors=True)
        return code


def sampler():
    """Every minute each container's CPU and memory, and the machine's load and free memory."""
    while True:
        try:
            out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], capture_output=True,
                                 text=True, timeout=50).stdout
            containers = [json.loads(line) for line in out.splitlines() if line.strip()]
            with open("/proc/meminfo") as f:
                mem = {line.split(":")[0]: int(line.split()[1]) for line in f}
            append(f"{STATS}/_samples.jsonl", {
                "t": round(time.time()), "load": os.getloadavg()[0], "mem_free_mb": mem["MemAvailable"] // 1024,
                "wrf": wrf_running(),
                "containers": [{"name": c.get("Name"), "cpu": c.get("CPUPerc"), "mem": c.get("MemUsage")} for c in containers],
            })
        except Exception as error:
            log(f"sampler: {error}")
        time.sleep(60)


def pull():
    result = subprocess.run(["flock", f"{REPO}/.git/runner.lock", "git", "-C", REPO, "pull", "-q", "--rebase", "--autostash"],
                            capture_output=True, text=True)
    if result.returncode:
        log(f"git pull failed: {result.stderr.strip()[:200]}")


def write_live(state, running, others, live, names, cache, now, wrf, budget, reserved, total_max, waiting_why, last_beat):
    """LIVE for the panel: running, due and waiting (why), next (when, why)."""
    lags = arrivals()
    out_running, out_waiting, out_next = [], [], []
    for name, run in running.items():
        out_running.append({"job": name, "priority": run.job.priority, "started": round(run.started), "live": run.live,
                            "source": run.source, "need": mem_need(run.job), "log": run.log_path, "heavy": run.job.heavy})
    for name in others:
        job = next((j for j in JOBS if j.name == name), None)
        logs = sorted((f for f in os.listdir(LOGS) if f.startswith(name + "-") and f[len(name) + 1:len(name) + 2].isdigit()), reverse=True)
        out_running.append({"job": name, "priority": job.priority if job else 2, "started": None, "live": name in live,
                            "need": mem_need(job) if job else None, "log": f"{LOGS}/{logs[0]}" if logs else None,
                            "heavy": bool(job and job.heavy)})
    busy = set(running) | set(others)
    for job in JOBS:
        if job.name in busy:
            continue
        if any(n not in names for n in job.needs):
            out_next.append({"job": job.name, "priority": job.priority, "at": None, "why": "needs " + ", ".join(job.needs)})
            continue
        wanted = due(job, state, cache, now)
        if wanted is not None:
            why = waiting_why.get(job.name) or (
                "priority 3 waits for a free slot" if job.priority >= 3 else
                f"all {total_max} slots busy" if len(busy) >= total_max else "starts within a minute")
            src = f"{datetime.fromtimestamp(wanted, timezone.utc):%d %H}Z run" if job.watch else None
            since = _due_since.setdefault(job.name, now)
            out_waiting.append({"job": job.name, "priority": job.priority, "why": why, "source": src, "need": mem_need(job),
                                "since": round(since)})
        else:
            _due_since.pop(job.name, None)
            at, why = expected(job, state, cache, now, lags)
            out_next.append({"job": job.name, "priority": job.priority, "at": round(at) if at else None, "why": why})
    out_next.sort(key=lambda n: (n["at"] is None, n["at"] or 0))
    save(LIVE, {"t": round(now), "host": os.uname().nodename, "wrf": wrf, "budget": budget, "reserved": reserved,
                "slots": total_max, "live": sorted(live), "beat": round(last_beat) or None,
                "running": out_running, "waiting": sorted(out_waiting, key=lambda w: w["priority"]), "next": out_next,
                "lags": {m: sorted(v)[len(v) // 2] for m, v in lags.items() if v}})


def main():
    for folder in (HOME, STATS, LOGS, WORK):
        os.makedirs(folder, exist_ok=True)
    state = load(STATE, {})
    try:
        with open(f"{STATS}/_arrivals.jsonl") as f:
            _arrived.update((a["model"], a["run"]) for a in map(json.loads, f.readlines()[-2000:]))
    except (OSError, ValueError, KeyError):
        pass
    running, last_pull, stamp, wrf_was, last_beat, beat_said, last_ops = {}, 0, code_stamp(), None, 0, False, 0
    threading.Thread(target=sampler, daemon=True).start()
    log(f"runner: {len(JOBS)} jobs, at most {MAX_TOTAL} at once ({MAX_HEAVY} heavy), work in {WORK}")
    while True:
        now = time.time()
        if now - last_pull > 600:
            pull()
            last_pull = now
            if code_stamp() != stamp:
                log("the runner's code changed: reloading (running jobs go on in their containers)")
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
            # The runs' logs a week (about 60 MB a day; the owner asked about the disks, 21.10.2026).
            for name in os.listdir(LOGS):
                path = f"{LOGS}/{name}"
                if name != "runner.log" and now - os.path.getmtime(path) > 7 * 86400:
                    os.remove(path)
        # Finished runs.
        for name, run in list(running.items()):
            code = run.finished()
            if code is None:
                continue
            del running[name]
            # Failures in a row (two leave the heartbeat's live list: GitHub runs the job meanwhile).
            mine = state.setdefault(name, {})
            mine["fails"] = 0 if code == 0 else mine.get("fails", 0) + 1
            save(STATE, state)
            append(f"{STATS}/_runs.jsonl", {"job": name, "due": round(run.wanted), "start": round(run.started),
                                            "end": round(time.time()), "exit": code, "live": run.live})
            log(f"{name}: done in {(time.time() - run.started) / 60:.1f} min, exit {code}")
        # WRF started or ended: the running jobs' CPU share follows.
        wrf = wrf_running()
        there = containers()
        if wrf != wrf_was:
            set_shares(there, LOW_SHARES if wrf else 1024)
            if wrf_was is not None:
                log(f"WRF {'started' if wrf else 'ended'}: the jobs' CPU share {LOW_SHARES if wrf else 1024}")
            wrf_was = wrf
        # Due runs, as many as allowed (containers left from before a reload count too).
        others = [name for name in there if name not in running]
        heavy_now = sum(1 for r in running.values() if r.job.heavy) + sum(1 for j in JOBS if j.name in others and j.heavy)
        # While WRF runs: all the slots, one big job (the owner, 21.10.2026: halving the slots left 14 jobs waiting,
        # spot points and rain now among them; the memory budget and the low CPU share keep WRF safe).
        total_max, heavy_max = (MAX_TOTAL, max(MAX_HEAVY // 2, 1)) if wrf else (MAX_TOTAL, MAX_HEAVY)
        names, live, cache = env_names(), live_jobs(), {}
        by_name = {j.name: j for j in JOBS}
        budget = mem_budget() - (WRF_MB if wrf else 0)
        # A running job counts its plan or what it uses now, whichever is bigger (a job without measured runs is
        # planned at a guess: mix-world at 1.5 GB used 4.0 during WRF, 21.10.2026).
        reserved = sum(max(mem_need(by_name[n]), used_mb(n)) for n in list(running) + others if n in by_name)
        for name in [n for n in _ids if n not in running and n not in others]:
            del _ids[name]
        waiting = None  # the priority of a due job left waiting for memory
        waiting_why = {}
        # The riders' first models first (the owner, 21.10.2026: the 00Z/12Z peaks); within a priority the
        # order of jobs.py.
        for job in sorted(JOBS, key=lambda j: j.priority):
            busy = len(running) + len(others)
            if job.name in running or job.name in others or busy >= total_max:
                continue
            # A slot kept for a 1 or 2 that comes in next minute.
            if job.priority >= 3 and busy >= total_max - 1:
                continue
            if job.heavy and heavy_now >= heavy_max:
                continue
            if any(n not in names for n in job.needs):
                continue
            wanted = due(job, state, cache, now)
            if wanted is None:
                continue
            need = mem_need(job)
            # A small job (SMALL_MB or less, mostly a few minutes) still goes when it fits: holding it back for a
            # bigger job waiting for memory left the spots' jobs queued behind it during WRF (21.10.2026).
            if waiting is not None and job.priority > waiting and (need > SMALL_MB or reserved + need > budget):
                waiting_why[job.name] = "a job of higher priority waits for memory"
                continue
            # Too big to fit beside WRF at all (the regional wind's 9.7 GB in 5 GB, 21.10.2026): waits for WRF to end,
            # never starts beside it, and holds nobody back meanwhile.
            if wrf and need > budget:
                waiting_why[job.name] = f"waits for WRF to end: needs {need / 1000:.1f} GB, {budget / 1000:.1f} for jobs while it runs"
                continue
            if busy and reserved + need > budget:
                waiting_why[job.name] = f"waits for memory: needs {need / 1000:.1f} GB, {max(budget - reserved, 0) / 1000:.1f} free"
                if waiting is None:
                    log(f"{job.name}: due, waiting for memory ({need} MB; {reserved} of {budget} taken)")
                waiting = job.priority if waiting is None else min(waiting, job.priority)
                continue
            reserved += need
            is_live = job.name in live
            running[job.name] = Running(job, is_live, wanted if job.watch else None, _due_since.pop(job.name, now),
                                        LOW_SHARES if wrf else 1024)
            mine = state.setdefault(job.name, {})
            mine["started"] = now
            if job.watch:
                mine["source"] = wanted
            save(STATE, state)
            heavy_now += job.heavy
            log(f"{job.name}: started ({'live' if is_live else 'shadow'})"
                + (f", source run {datetime.fromtimestamp(wanted, timezone.utc):%d %H}Z" if job.watch else ""))
        try:
            write_live(state, running, others, live, names, cache, now, wrf, budget, reserved, total_max, waiting_why, last_beat)
        except Exception as error:
            log(f"live.json: {error}")
        if now - last_ops >= 120:
            last_ops = now
            try:
                keep_ops()
            except Exception as error:
                log(f"ops: {error}")
        if now - last_beat >= 300:
            last_beat = now
            if not heartbeat(state, live, set(running) | set(others), wrf) and not beat_said:
                log("no heartbeat: INGEST_TOKEN not in the env file yet (GitHub runs everything meanwhile)")
                beat_said = True
        time.sleep(60)


if __name__ == "__main__":
    main()
