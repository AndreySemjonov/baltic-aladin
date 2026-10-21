"""The helper's own scheduler (the owner, 21.10.2026: production schedules off GitHub; the Tower now, the
Mac mini from the end of the week; GitHub only for the code, tests and deploys). Every minute: each job in
runner/jobs.py that's due (its source has a newer complete run, or its interval passed) starts in its own
container (runner/Dockerfile; measured by runner/measure.py), at most MAX_TOTAL at once and MAX_HEAVY big
ones (fewer while WRF runs on this machine). The repo is pulled every 10 minutes (the code is mounted, not
built in).

Shadow mode (the default): every job writes to --local under the work folder, removed after it's measured,
nothing uploaded; GitHub stays live. A job named in LIVE_FILE runs live instead (uploads with INGEST_TOKEN
from ENV_FILE): the switch, one job at a time, its GitHub schedule turned off then.

While WRF runs here (its lock), the jobs' containers get a low CPU share (LOW_SHARES of Docker's 1024; the
owner, 21.10.2026: WRF keeps the fast cores, the jobs use what's left: the slow cores and the fast ones'
second threads), set again on the running ones as WRF starts and ends. The scheduler reloads itself when its
code changes (after a pull); a job whose container is still there (mg-<job>, e.g. from before a reload)
counts as running.

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
STATS, LOGS, STATE = f"{HOME}/stats", f"{HOME}/logs", f"{HOME}/state.json"
ENV_FILE = os.environ.get("MG_ENV", "/root/.meteogust.env")
LIVE_FILE = os.environ.get("MG_LIVE", "/root/.meteogust-live")
IMAGE = os.environ.get("MG_IMAGE", "meteogust-helper")
MAX_TOTAL, MAX_HEAVY = int(os.environ.get("MG_MAX", "4")), int(os.environ.get("MG_MAX_HEAVY", "2"))
WRF_LOCK = "/root/wrf-logs/.lock"
LOW_SHARES = int(os.environ.get("MG_LOW_SHARES", "128"))
HERE = os.path.dirname(os.path.abspath(__file__))
BUCKET = "https://openmeteo.s3.amazonaws.com/data_spatial/"
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


def newest_run(model, cache):
    """A model's newest complete run on Open-Meteo's open data (unix seconds), None when unknown."""
    if model not in cache:
        try:
            request = urllib.request.Request(f"{BUCKET}{model}/latest.json", headers=AGENT)
            with urllib.request.urlopen(request, timeout=20) as reply:
                latest = json.load(reply)
            run = datetime.fromisoformat(latest["reference_time"].replace("Z", "+00:00")).timestamp() if latest.get("completed") else None
            done = datetime.fromisoformat(latest["last_modified_time"].replace("Z", "+00:00")).timestamp()
            cache[model] = (run, done)
        except Exception:
            cache[model] = (None, None)
    return cache[model]


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
        command = ["docker", "run", "--rm", "--name", self.name, "--cpu-shares", str(shares), "-e", f"MG_JOB={job.name}",
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


def main():
    for folder in (HOME, STATS, LOGS, WORK):
        os.makedirs(folder, exist_ok=True)
    state = load(STATE, {})
    running, last_pull, stamp, wrf_was = {}, 0, code_stamp(), None
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
        total_max, heavy_max = (max(MAX_TOTAL // 2, 1), max(MAX_HEAVY // 2, 1)) if wrf else (MAX_TOTAL, MAX_HEAVY)
        names, live, cache = env_names(), live_jobs(), {}
        for job in JOBS:
            if job.name in running or job.name in others or len(running) + len(others) >= total_max:
                continue
            if job.heavy and heavy_now >= heavy_max:
                continue
            if any(n not in names for n in job.needs):
                continue
            wanted = due(job, state, cache, now)
            if wanted is None:
                continue
            is_live = job.name in live
            running[job.name] = Running(job, is_live, wanted if job.watch else None, now, LOW_SHARES if wrf else 1024)
            mine = state.setdefault(job.name, {})
            mine["started"] = now
            if job.watch:
                mine["source"] = wanted
            save(STATE, state)
            heavy_now += job.heavy
            log(f"{job.name}: started ({'live' if is_live else 'shadow'})"
                + (f", source run {datetime.fromtimestamp(wanted, timezone.utc):%d %H}Z" if job.watch else ""))
        time.sleep(60)


if __name__ == "__main__":
    main()
