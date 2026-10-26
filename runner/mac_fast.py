"""The fast jobs on the Mac mini (the owner, 26.10.2026: "if we do not have free resources on Tower we have to use mac
mini. This fast updated jobs is important to release in time"; "Agree about B, but we need to be sure that there will
be enough resources for development too during the daytime"): the jobs.py entries marked mac=True (the radar's
GeoTIFFs, the satellite clouds, the rain nowcast, Kolka's station), run here at their own rhythm, the Tower their
backup (scheduler.py runs one when this Mac's beat is over 10 minutes old or the job hasn't finished well here within
its interval and 10 minutes).

Development first: launchd starts this every minute as a background process (com.meteogust.fast.plist, ProcessType
Background) and each job under `taskpolicy -b`: macOS keeps them on the M4's 6 efficiency cores at low I/O priority,
Xcode, the simulators and the builds keep the 4 performance cores. A job starts only with the memory pressure normal
and 25% free (40% for the bigger ones, planned over 500 MB, while Xcode or a simulator runs); left to the Tower
otherwise. Two lanes, each one job at a time (26.10.2026: in shadow the nowcast's 452 s and the clouds' 318 s held up
Kolka and the radar): the tiny jobs (jobs.py tiny: Kolka, the radar's GeoTIFFs) and the others (the clouds, the
nowcast), each lane with its own lock, so at most one big job runs. Shadow first: a job publishes only while named in ~/.meteogust-fast-live (else --local under the work
folder, removed after). Each job's last run in ~/meteogust-fast/state.json, which mac_beat.py sends in runner/mac.json.

    python runner/mac_fast.py          (launchd, every minute; one at a time by a lock)
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from jobs import JOBS  # noqa: E402

HOME = Path.home() / "meteogust-fast"
STATE, LOGS, WORK = HOME / "state.json", HOME / "logs", HOME / "work"
LIVE_FILE = Path.home() / ".meteogust-fast-live"
ENV_FILE = Path.home() / ".meteogust.env"
PYTHON = str(HOME / "venv" / "bin" / "python")
WRF_STATE = Path.home() / "wrf-logs" / ".state-tarifa.json"
PULL_EVERY = 600
BIG_MB = 500
# Development going on: Xcode's build, the simulators, the test runner.
DEV = ("xcodebuild", "XCBBuildService", "SWBBuildService", "swift-frontend", "Simulator", "launchd_sim", "maestro")


def log(text):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {text}", flush=True)


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    tmp = Path(f"{path}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def update(name, fields=None, drop=()):
    """One job's (or a root key's) fields merged into the state file under its lock: the two lanes write it at once."""
    with open(HOME / ".state.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(STATE, {})
        if isinstance(fields, dict):
            mine = state.setdefault(name, {})
            mine.update(fields)
            for k in drop:
                mine.pop(k, None)
        else:
            state[name] = fields
        save(STATE, state)
        return state


def env():
    out = dict(os.environ)
    try:
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    out["PYTHONPATH"] = str(REPO)
    return out


def memory():
    """(the pressure level: 1 normal, 2 warning, 4 critical; the free share, 0-1)."""
    level = int(subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True).stdout.strip() or 1)
    text = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True).stdout
    free = next((int(w.rstrip("%")) / 100 for line in text.splitlines() if "free percentage" in line for w in line.split() if w.endswith("%")), 0.5)
    return level, free


def developing():
    names = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True).stdout.split("\n")
    return any(os.path.basename(n.strip()) in DEV for n in names)


def wrf_going():
    state = load(WRF_STATE, {})
    return state.get("stage") not in (None, "done", "failed") and time.time() - state.get("t", 0) < 3 * 3600


def pull(state):
    """The code every 10 minutes (not while WRF runs: its own pull at its start)."""
    if time.time() - state.get("_pulled", 0) < PULL_EVERY or wrf_going():
        return
    update("_pulled", time.time())
    result = subprocess.run(["git", "-C", str(REPO), "pull", "-q", "--rebase", "--autostash"], capture_output=True, text=True)
    if result.returncode:
        log(f"git pull failed: {result.stderr.strip()[:200]}")


def planned_mb(name):
    """Its usual peak here, from its last runs (state), else jobs.py's mem_mb, else 1500."""
    peaks = load(STATE, {}).get(name, {}).get("peaks") or []
    return max(peaks) if peaks else next((j.mem_mb for j in JOBS if j.name == name), 0) or 1500


def run(job, live):
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M}"
    LOGS.mkdir(parents=True, exist_ok=True)
    out = WORK / job.name
    script = [PYTHON, *job.args] + ([] if live else ["--local", str(out)])
    started = time.time()
    update(job.name, {"started": round(started), "running": True, "live": live})
    with open(LOGS / f"{job.name}-{stamp}.log", "w") as f:
        try:
            # time -l: its peak memory into the log's end, for the plan.
            result = subprocess.run(["/usr/bin/time", "-l", "taskpolicy", "-b", *script], cwd=REPO, env=env(), stdout=f,
                                    stderr=subprocess.STDOUT, timeout=job.timeout * 60)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    peak = None
    try:
        for line in (LOGS / f"{job.name}-{stamp}.log").read_text(errors="replace").splitlines()[-30:]:
            if "maximum resident set size" in line:
                peak = round(int(line.split()[0]) / 1048576)
    except (OSError, ValueError):
        pass
    if not live:
        shutil.rmtree(out, ignore_errors=True)
    fields = {"ended": round(time.time()), "exit": code, "running": False}
    if code == 0:
        fields["ok"] = round(time.time())
    if peak:
        fields["peaks"] = (load(STATE, {}).get(job.name, {}).get("peaks") or [])[-9:] + [peak]
    update(job.name, fields)
    log(f"{job.name}: {'live' if live else 'shadow'}, exit {code}, {time.time() - started:.0f} s" + (f", {peak} MB" if peak else ""))


def tidy():
    cut = time.time() - 3 * 86400
    for path in LOGS.glob("*.log"):
        if path.stat().st_mtime < cut:
            path.unlink()


def lane(tiny, live):
    """The due jobs of one lane (tiny or not), one at a time, under the lane's own lock (a run of it still going: none)."""
    with open(HOME / (".lock-tiny" if tiny else ".lock-big"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return  # the lane's run before still going
        state = load(STATE, {})
        now = time.time()
        due = [j for j in JOBS if getattr(j, "mac", False) and bool(j.tiny) == tiny
               and now - state.get(j.name, {}).get("started", 0) >= j.every * 60]
        for job in sorted(due, key=lambda j: (j.priority, state.get(j.name, {}).get("started", 0))):
            level, free = memory()
            dev = developing()
            need = 0.40 if dev and planned_mb(job.name) > BIG_MB else 0.25
            if level != 1 or free < need:
                why = f"held: memory {'pressure' if level != 1 else f'{free:.0%} free'}{' (development going on)' if dev else ''}, left to the Tower"
                if state.get(job.name, {}).get("held") != why:
                    log(f"{job.name}: {why}")
                update(job.name, {"held": why})
                continue
            if state.get(job.name, {}).get("held"):
                update(job.name, {}, drop=("held",))
            run(job, job.name in live)


def main():
    HOME.mkdir(parents=True, exist_ok=True)
    state = load(STATE, {})
    try:
        live = {line.strip() for line in LIVE_FILE.read_text().splitlines() if line.strip() and not line.startswith("#")}
    except OSError:
        live = set()
    # The big lane in a process of its own (launchd starts this again only once it has ended: a big job's minutes in it
    # would hold the tiny lane up), the tiny lane here, each minute.
    if "--big" in sys.argv:
        lane(False, live)
        return
    pull(state)
    with open(HOME / "runner.log", "a") as out:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--big"], cwd=REPO, stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True)
    lane(True, live)
    if time.time() - state.get("_tidied", 0) > 3600:
        update("_tidied", time.time())
        tidy()


if __name__ == "__main__":
    main()
