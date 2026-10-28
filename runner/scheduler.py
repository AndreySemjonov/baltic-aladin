"""The helper's own scheduler (the owner, 21.10.2026: production schedules off GitHub; the Tower now, the
Mac mini from the end of the week; GitHub only for the code, tests and deploys). Every minute: each job in
runner/jobs.py that's due (its source has a newer complete run, or its interval passed) starts in its own
container (runner/Dockerfile; measured by runner/measure.py), at most MAX_TOTAL at once and MAX_HEAVY big
ones (one while WRF runs on this machine), live jobs before shadow ones, in the jobs' priority order (jobs.py; a priority 3 only while
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
from jobs import INPUT_RUNS, JOBS  # noqa: E402

REPO = os.environ.get("MG_REPO", "/root/baltic-aladin")
HOME = os.environ.get("MG_HOME", "/var/lib/meteogust")
WORK = os.environ.get("MG_WORK", "/mnt/d/meteogust/runner-work")
STATS, LOGS, STATE, LIVE = f"{HOME}/stats", f"{HOME}/logs", f"{HOME}/state.json", f"{HOME}/live.json"
# The runs going on, written just before a reload and taken over by the reloaded runner (Adopted); removed once read.
HANDOVER = f"{HOME}/handover.json"
ENV_FILE = os.environ.get("MG_ENV", "/root/.meteogust.env")
LIVE_FILE = os.environ.get("MG_LIVE", "/root/.meteogust-live")
IMAGE = os.environ.get("MG_IMAGE", "meteogust-helper")
# Big jobs at once: 5 outside WRF, 1 beside it. 3 from 23.10.2026 (2 held the seas back for hours with the machine half
# idle; memory is the real-memory check's since 22.10); 5 since 28.10.2026, the Tower at 64 GB (WSL 54): World ECMWF 12Z
# waited 18:28-18:47 UTC for a big job's place with 38 of 54 GB planned and CPU under 75% (the owner: "We still have some
# waiting even with 64gb ram?", "Yes, raise to 5").
MAX_TOTAL, MAX_HEAVY = int(os.environ.get("MG_MAX", "4")), int(os.environ.get("MG_MAX_HEAVY", "5"))
# Outside WRF the slots follow the machine (the owner, 22.10.2026, from the Pipeline's machine band: with WRF off jobs
# waited 314 of 342 minutes while CPU was under 50% and over 6 GB free): beyond MAX_TOTAL, up to MAX_FLEX at once
# while the machine's CPU over the last minute is under FLEX_CPU %, one more a minute (a new job's load shows the
# minute after); the memory budget still decides. While WRF runs: MAX_TOTAL as before.
MAX_FLEX = int(os.environ.get("MG_MAX_FLEX", "8"))
FLEX_CPU = float(os.environ.get("MG_FLEX_CPU", "75"))
# A priority-3 job due this long counts as a 2 (the same day: the world weather mix had never run, the waves and the
# ensembles not in a day).
AGE_AFTER = 60 * 60
WRF_LOCK = "/root/wrf-logs/.lock"
REGION = os.environ.get("MG_REGION", "eu")
LOW_SHARES = int(os.environ.get("MG_LOW_SHARES", "128"))
# The jobs' CPU share by priority, of Docker's 1024 (LOW_SHARES while WRF runs): when the machine is full a priority-1
# job (the Mix, the series, the spot points) gets the CPU first and a big priority-3 one (the world weather mix, the
# sea) what is left; on a quiet machine each still takes all it can. The owner, 29.10.2026 ("Yes, A then B"): the Mix
# took 48 min instead of ~16 beside the world weather mix, the sea, UKV and the regional jobs (00:05-00:53 Riga).
PRIORITY_SHARE = {1: 2.0, 2: 1.0, 3: 0.25}


def cpu_shares(job, wrf):
    return max(2, round((LOW_SHARES if wrf else 1024) * PRIORITY_SHARE.get(job.priority, 1.0)))
# The jobs' memory: all but this much of the machine's (MB), unless MG_MEM_MB says.
MEM_SPARE = 1800
MEM_DEFAULT = {True: 6000, False: 1500}
SMALL_MB = 1500
# After this long waiting for memory a job gets the machine drained for it: nothing new starts until it fits (the
# Mix, 11.8 GB, never started after WRF, 21.10.2026: small jobs kept slipping in, series running nearly nonstop).
DRAIN_AFTER = 20 * 60
# WRF's own memory while it runs here (the Tower's 1 km Tarifa run: 8.4 GB), not counted in the jobs' peaks.
WRF_MB = int(os.environ.get("MG_WRF_MB", "9000"))
# Memory by what the machine really has (the owner, 22.10.2026, "A, B, C": 12 h of waits showed 2-2.6 GB left on
# paper while 11-13 GB were free; each job held its highest peak of 10 runs for its whole run). A job plans for its
# usual peak (mem_need), starts while the machine really has that and MEM_MARGIN free (less what the jobs started in
# the last RAMP_S still have to take), and the plans may add up to OVERCOMMIT x the budget outside WRF (1x beside
# it). A job's container may use its record peak x 1.3 at most (mem_limit; with an oom-score that makes the kernel pick
# a job before WRF), so a rare double peak stops that one job.
# Outside WRF a spiky job plans below its peak (the owner, 23.10.2026: "swap should be best for memory peaks"; mem_shapes):
# the waves hold 8.6 GB for 3 of their 33 minutes, so the plan is the level a job stays above for a fifth of its run at
# most and the short peak may borrow swap. Flat jobs (the mixes, world ECMWF) plan as before; beside WRF every job plans
# its whole peak (WRF must never swap).
# A failed run tried again after RETRY_S, up to RETRIES in a row (the same model run for a watching job), then its
# usual rhythm (22.10.2026: the waves' first run, killed out of memory at 02:35 UTC, would have waited 12 h; a
# watching job marked its failed run seen and never made it).
RETRY_S, RETRIES = 45 * 60, 3
# Our own map packs kept on D: for the jobs (packcache.py; the owner, 23.10.2026: "Build on D"): mounted at /cache in
# every job's container, trimmed to CACHE_GB every 10 minutes, least recently used first (a read touches a file). The
# jobs read each run's newest packs, so more room mostly keeps packs nobody asks for again: the hit rate in
# STATS/_cache/<job>.json says whether it's enough.
CACHE = os.environ.get("MG_CACHE_DIR", "/mnt/d/meteogust/cache")
CACHE_GB = float(os.environ.get("MG_CACHE_GB", "30"))
OVERCOMMIT = float(os.environ.get("MG_OVERCOMMIT", "1.25"))
MEM_MARGIN = 2000
RAMP_S = 10 * 60
JOB_OOM_SCORE = 500
FILES = "https://files.meteogust.com/"
HERE = os.path.dirname(os.path.abspath(__file__))
BUCKET = "https://openmeteo.s3.amazonaws.com/data_spatial/"
HEARTBEAT = "https://data.meteogust.com/tiles/upload/runner/heartbeat.json"
# The ops panel: its server (127.0.0.1:OPS_PORT, the tunnel's origin) and the jobs' download reports (UDP to the
# Docker bridge's host address); the tunnel's container.
OPS_PORT, OPS_UDP = 8090, 8091
OPS_HOST = os.environ.get("MG_OPS_HOST", "172.17.0.1")
TUNNEL = "meteogust-tunnel"
# A version two weeks old or more (Cloudflare marks old connectors "Unsupported": 2024.12.2 was, 21.10.2026).
# 2026.10.3 (the owner, 26.10.2026, Cloudflare's notice: before 2026.10.2 cloudflared trusts eight eMudhra intermediate
# certificates with weak keys); keep_ops replaces the running container when this changes.
TUNNEL_IMAGE = "cloudflare/cloudflared:2026.10.3"
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


_cpu_last = [None]


def cpu_busy():
    """The machine's CPU busy share (%) since the last call (the loop's minute), from /proc/stat; None the first time."""
    try:
        with open("/proc/stat") as f:
            values = [int(v) for v in f.readline().split()[1:9]]
    except (OSError, ValueError):
        return None
    idle, total = values[3] + values[4], sum(values)
    last, _cpu_last[0] = _cpu_last[0], (idle, total)
    if not last or total <= last[1]:
        return None
    return 100.0 * (1 - (idle - last[0]) / (total - last[1]))


def trim(path, keep):
    """A record file cut to its last `keep` lines (the minute records grew without end)."""
    try:
        with open(path) as f:
            lines = f.readlines()
        if len(lines) > keep * 1.2:
            with open(path + ".tmp", "w") as f:
                f.writelines(lines[-keep:])
            os.replace(path + ".tmp", path)
    except OSError:
        pass


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
# The live file's jobs kept in shadow for a missing variable, as last logged (logged again only when it changes).
_held_said = set()
# When the queue was last recorded (STATS/_queue.jsonl, once a minute: the panel's machine band; the owner, 22.10.2026:
# "with the scheduler recording why jobs wait").
_queue_at = [0.0]


def why_kind(why):
    """A waiting job's reason as a word for the record: slots, priority3, memory, behind (a higher priority waits for
    memory), drain (held for one waiting long), wrf (too big beside WRF), planned (the jobs' plans over their cap while
    the machine has room), starting."""
    if why.startswith("held:"):
        return "drain"
    if why.startswith("waits for WRF"):
        return "wrf"
    if why.startswith("waits for planned memory"):
        return "planned"
    if why.startswith("big jobs:"):
        return "heavy"
    if why.startswith("waits for memory"):
        return "memory"
    if why.startswith("a job of higher priority") or why.startswith("a live job waits"):
        return "behind"
    if why.startswith("priority 3"):
        return "priority3"
    if why.startswith("all ") and "slots busy" in why:
        return "slots"
    return "starting"


def made_nothing(path, took):
    """A run that found nothing new: done within 3 minutes with at most 3 lines said ("ENS ... already published",
    "Water: nothing new", "Estonian gauges read 40 min ago"); a run that makes something says dozens. The panel paints
    no wait before it (the owner, 29.10.2026: "there still are some long waitings", most were these re-checks)."""
    if took >= 180:
        return False
    try:
        with open(path, errors="replace") as f:
            return sum(1 for line in f if line.strip()) <= 3
    except OSError:
        return False


_fetch_fails = {}  # model -> latest.json failures in a row (logged at the 3rd)


def latest_done(latest, headers):
    """When a run became downloadable: latest.json's own write on S3 (Last-Modified; Open-Meteo writes it after the
    run's last file), else its last_modified_time. That field is when Open-Meteo finished converting, before the upload:
    ICON-EU 12Z on 23.10.2026 said 15:39:44 UTC, its +120 h file and meta.json reached S3 at 15:58:44 and 15:59:20, and
    the scheduler saw it at 15:59:51; the "20 minutes late" was the upload (the 30 h runs, 03/09/15/21Z, upload in a
    minute or two)."""
    import email.utils
    try:
        return email.utils.parsedate_to_datetime(headers["Last-Modified"]).timestamp()
    except (KeyError, TypeError, ValueError):
        made = latest.get("last_modified_time")
        return datetime.fromisoformat(made.replace("Z", "+00:00")).timestamp() if made else None


def newest_run(model, cache):
    """A model's newest complete run on Open-Meteo's open data (unix seconds) and when it became downloadable
    (latest_done), None when unknown; asked at most every 2 minutes. A run not seen before goes in
    STATS/_arrivals.jsonl (when it came: the panel's expected times; "made", Open-Meteo's own last_modified_time)."""
    if model not in cache and model in _runs_seen and time.time() - _runs_seen[model][0] < 120:
        cache[model] = _runs_seen[model][1]
    if model not in cache:
        try:
            request = urllib.request.Request(f"{BUCKET}{model}/latest.json", headers=AGENT)
            with urllib.request.urlopen(request, timeout=20) as reply:
                latest = json.load(reply)
                done = latest_done(latest, reply.headers)
            run = datetime.fromisoformat(latest["reference_time"].replace("Z", "+00:00")).timestamp() if latest.get("completed") else None
            cache[model] = (run, done)
            _runs_seen[model] = (time.time(), (run, done))
            _fetch_fails.pop(model, None)
            if run and (model, round(run)) not in _arrived:
                # latest.json's write is when the run became complete on S3, whenever we look.
                made = latest.get("last_modified_time")
                append(f"{STATS}/_arrivals.jsonl", {"model": model, "run": round(run), "done": round(done or time.time()),
                                                   "seen": round(time.time()),
                                                   **({"made": round(datetime.fromisoformat(made.replace("Z", "+00:00")).timestamp())} if made else {})})
                _arrived.add((model, round(run)))
        except Exception as error:
            cache[model] = (None, None)
            _fetch_fails[model] = _fetch_fails.get(model, 0) + 1
            if _fetch_fails[model] == 3:
                log(f"{model}: latest.json not read 3 times in a row ({error})")
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
    if 0 < mine.get("fails", 0) <= RETRIES and mine.get("started"):
        return mine["started"] + RETRY_S, f"tried again after a failed run ({mine['fails']} of {RETRIES})"
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
    if job.every and job.inputs:
        what = f"{len(job.inputs)} sets" if not job.inputs[0].startswith("http") else "its source's new run"
        return mine.get("started", now) + job.every * 60, f"on a new input ({what}), else every {job.every} min"
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


_mac_seen = [0.0, None]  # (when read, the Mac's beat)


def mac_has(job, now):
    """Whether the Mac mini makes this job now (jobs.py mac): its beat (runner/mac.json, read at most once a minute) under
    10 minutes old, the job live there and running or finished well within its interval and 10 minutes."""
    if not job.mac:
        return False
    if now - _mac_seen[0] > 60:
        _mac_seen[0] = now
        try:
            request = urllib.request.Request(f"{FILES}runner/mac.json?t={int(now)}", headers=AGENT)
            with urllib.request.urlopen(request, timeout=10) as reply:
                _mac_seen[1] = json.load(reply)
        except Exception:
            _mac_seen[1] = None
    beat = _mac_seen[1]
    if not beat or now - beat.get("t", 0) > 600:
        return False
    mine = (beat.get("fast") or {}).get(job.name) or {}
    if not mine.get("live"):
        return False
    return bool(mine.get("running")) or now - mine.get("ok", 0) <= job.every * 60 + 600


def due(job, state, cache, now, live=False):
    """The run (or moment) a job is due for, None when it isn't."""
    # Made on the Mac mini now (jobs.py mac): not here.
    if mac_has(job, now):
        return None
    mine = state.get(job.name, {})
    since = now - mine.get("started", 0)
    retry = 0 < mine.get("fails", 0) <= RETRIES and since >= RETRY_S
    # Live now, its last run in shadow (published nothing): due at once, its newest run (the owner, 23.10.2026: "Europe's
    # seas show late. why": the seas' last run was shadow at 05:52 UTC, live from 07:00, GitHub's 09:20 run stood aside,
    # and the 12 h timer from the shadow run left the map at 00:31 UTC until 17:52). A run published already (GitHub's)
    # is skipped by the job itself in a minute.
    switched = live and mine.get("started") and mine.get("live") is False
    if job.watch:
        best = None
        sources = mine.get("sources") or {}
        for model, every in job.watch:
            run, done = newest_run(model, cache)
            if run is None or datetime.fromtimestamp(run, timezone.utc).hour % every:
                continue
            if done and now - done < job.delay * 3600:
                continue
            # Each watched model against its own last run (one number for all: ICON-D2's 12Z against MET Nordic's
            # 13Z never came due, 21.10.2026). Not recorded yet: the job's one source (one model), else new when it
            # became complete after the job last started.
            seen = sources.get(model)
            if seen is None:
                seen = mine.get("source", 0) if len(job.watch) == 1 else (run if done and done <= mine.get("started", 0) else 0)
            if switched:
                seen = 0
            if (run > seen or (retry and run >= seen)) and (best is None or run > best):
                best = run
        # A watched job with `every` too: due at that interval even with nothing new in the watched file, for a job
        # whose script reads its maker's own files (23.10.2026: GFS 12Z was out at NOAA, world_tiles.py's source, while
        # Open-Meteo's copy stood at 32 of 209 steps from 16:03 UTC, and with GitHub standing aside riders kept 06Z).
        # Its source stays the newest seen (the script finds what's new itself, or exits: published already).
        if best is None and job.every and since >= job.every * 60:
            known = [r for r in (newest_run(model, cache)[0] for model, _ in job.watch) if r]
            if known or mine.get("source"):
                return max(known + [mine.get("source") or 0])
        return best
    if retry or switched:
        return now
    # One of its inputs published since it last started (D), its min_gap after that start at the earliest.
    if job.inputs and mine.get("inputs") and since >= job.min_gap * 60:
        seen = mine["inputs"]
        if any(input_stamp(s) not in (None, seen.get(s)) for s in job.inputs):
            return now
    if job.every and since >= job.every * 60:
        return now
    return None


def mem_budget():
    if os.environ.get("MG_MEM_MB"):
        return int(os.environ["MG_MEM_MB"])
    with open("/proc/meminfo") as f:
        total = next(int(line.split()[1]) for line in f if line.startswith("MemTotal"))
    return total // 1024 - MEM_SPARE


def mem_peaks(job, _cache={}):
    """(usual, limit) of a job's memory (MB) from its last 10 runs' peaks, read every 10 minutes: usual = the 3rd quarter's
    peak x 1.2 (one outlier left out, not the runs that found nothing new: ecmwf-short's median was 0.1 GB of its 3),
    never over the most x 1.15 (the plan before; with under 3 runs that); limit = the most x 1.3, and with under 5 runs
    at least twice the usual (one run says little), never under 1.5 GB; a default while it has none."""
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
    peaks = sorted(p for p in peaks if p > 0)
    if not peaks:
        value = (job.mem_mb, 1500) if job.tiny and job.mem_mb else (MEM_DEFAULT[job.heavy], MEM_DEFAULT[job.heavy] * 2)
    else:
        most = peaks[-1] * 1.15
        usual = min(peaks[len(peaks) * 3 // 4] * 1.2, most) if len(peaks) >= 3 else most
        limit = max(peaks[-1] * 1.3, usual * (2 if len(peaks) < 5 else 1), 1500)
        value = (int(usual), int(limit))
    # jobs.py's floor for a job whose shape changed (its old peaks too low: 23.10.2026, ECMWF's --workers).
    if job.mem_mb:
        value = (max(value[0], job.mem_mb), max(value[1], int(job.mem_mb * 1.3)))
    _cache[job.name] = (now, value)
    return value


# The share of its peak a job plans for outside WRF: the level its run's minute samples stay above SHAPE_TOP of the time
# at most, as a share of that run's peak; the median of its last 10 finished runs (at least SHAPE_RUNS), never under
# SHAPE_MIN. From the last SHAPE_DAYS of STATS/_samples.jsonl (it keeps about 8), read every 10 minutes. Two runs are
# enough: the jobs this is for (waves, seas) run twice a day. The minute samples miss the sharpest peaks (the waves:
# 15.9 GB in measure.py's record, 8.6 in the samples), so the share is applied to the record's usual peak (mem_peaks).
SHAPE_TOP, SHAPE_RUNS, SHAPE_MIN, SHAPE_DAYS = 0.2, 2, 0.3, 8
_shape = {"t": 0, "share": {}, "wrf": False}  # "wrf": the main loop's, for mem_need
_DOCKER_UNITS = {"B": 2**-20, "KiB": 2**-10, "kB": 2**-10, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}


def docker_mb(text):
    """docker stats' "4.633GiB / 6.516GiB" -> 4744 (MB used), None when it can't be read."""
    try:
        used = (text or "").split("/")[0].strip()
        number = used.rstrip("BKMGiTkb")
        return float(number) * _DOCKER_UNITS[used[len(number):]]
    except (ValueError, KeyError):
        return None


def mem_shapes():
    """{job: share of its peak it plans for outside WRF} for the jobs with enough measured runs (mem_need)."""
    now = time.time()
    if now - _shape["t"] < 600:
        return _shape["share"]
    runs, last_seen = {}, {}  # job -> [[minute samples (MB)], ...]; job -> the time of its last sample
    try:
        with open(f"{STATS}/_samples.jsonl") as f:
            for line in f:
                try:
                    sample = json.loads(line)
                except ValueError:
                    continue
                t = sample.get("t")
                if not isinstance(t, (int, float)) or now - t > SHAPE_DAYS * 86400:
                    continue
                for c in sample.get("containers", []):
                    name, mb = c.get("name") or "", docker_mb(c.get("mem"))
                    if not name.startswith("mg-") or mb is None:
                        continue
                    job = name[3:]
                    # Over 3 minutes without a sample: a new run.
                    if t - last_seen.get(job, 0) > 180:
                        runs.setdefault(job, []).append([])
                    runs[job][-1].append(mb)
                    last_seen[job] = t
    except OSError:
        pass
    share = {}
    for job, job_runs in runs.items():
        # Not the run still going (its peak may be ahead).
        finished = job_runs[:-1] if now - last_seen[job] < 180 else job_runs
        shares = []
        for run in finished[-10:]:
            if len(run) >= 5 and max(run) > 0:
                shares.append(sorted(run)[int(len(run) * (1 - SHAPE_TOP))] / max(run))
        if len(shares) >= SHAPE_RUNS:
            share[job] = round(max(sorted(shares)[len(shares) // 2], SHAPE_MIN), 2)
    changed = {j: s for j, s in share.items() if abs(s - _shape["share"].get(j, 1)) >= 0.1}
    if changed:
        log("memory shapes (share of the peak planned outside WRF): "
            + ", ".join(f"{j} {s:.2f}" for j, s in sorted(changed.items(), key=lambda item: item[1])))
    _shape.update(t=now, share=share)
    return share


def mem_need(job):
    """A job's planned memory (MB): its usual peak (mem_peaks), outside WRF times its shape (mem_shapes)."""
    usual = mem_peaks(job)[0]
    if _shape["wrf"]:
        return usual
    return int(usual * mem_shapes().get(job.name, 1))


def mem_limit(job):
    """A job's container memory limit (MB): its record peak x 1.3 (mem_peaks)."""
    return mem_peaks(job)[1]


def mem_available():
    """What the machine really has free now (MB, /proc/meminfo MemAvailable)."""
    try:
        with open("/proc/meminfo") as f:
            return next(int(line.split()[1]) for line in f if line.startswith("MemAvailable")) // 1024
    except (OSError, StopIteration, ValueError):
        return None


_inputs_seen = {}


def input_runs(job):
    """The source runs a job's inputs hold as it starts ({Open-Meteo's model: run, Unix seconds}; INPUT_RUNS), what
    a watched model's run is for the others; {} when unread."""
    out = {}
    for url in job.inputs:
        if url not in INPUT_RUNS:
            continue
        model, ask = INPUT_RUNS[url]
        try:
            with urllib.request.urlopen(urllib.request.Request(ask, headers=AGENT), timeout=20) as reply:
                # OPeNDAP's ASCII: the variable's line last ("forecast_reference_time, 1.7930412E9").
                out[model] = round(float(reply.read(4096).decode().strip().splitlines()[-1].split(",")[-1]))
        except (OSError, ValueError, IndexError):
            pass
    return out


def input_stamp(set_name):
    """A map set's last publish ("updated", else its run) from its index (through Cloudflare's 2-minute cache), or for a
    URL a fingerprint of its answer (MEPS's description: its creation time); asked at most every 2 minutes; None when
    unknown."""
    seen = _inputs_seen.get(set_name)
    if seen and time.time() - seen[0] < 120:
        return seen[1]
    stamp = None
    try:
        url = set_name if set_name.startswith("http") else f"{FILES}tiles/{set_name}/index.json"
        request = urllib.request.Request(url, headers=AGENT)
        with urllib.request.urlopen(request, timeout=20) as reply:
            body = reply.read(1 << 20)
        if set_name.startswith("http"):
            import hashlib
            stamp = hashlib.sha1(body).hexdigest()[:16]
        else:
            index = json.loads(body)
            stamp = index.get("updated") or index.get("runName") or index.get("run")
    except Exception:
        stamp = seen[1] if seen else None
    _inputs_seen[set_name] = (time.time(), stamp)
    return stamp


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


def set_shares(names, wrf):
    for name in names:
        job = next((j for j in JOBS if j.name == name), None)
        if job:
            subprocess.run(["docker", "update", "--cpu-shares", str(cpu_shares(job, wrf)), f"mg-{name}"], capture_output=True)


def reap_strays(keep):
    """The runner's ended children from before its reloads, left unwaited (27.10.2026: 132 docker clients, zombies of
    the runner: each reload before the hand-over left its runs' clients behind). The runs handed over (keep) aren't
    touched: their exit codes are theirs."""
    reaped = 0
    for pid in filter(str.isdigit, os.listdir("/proc") if os.path.isdir("/proc") else []):
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            if fields[0] == "Z" and int(fields[1]) == os.getpid() and int(pid) not in keep:
                reaped += os.waitpid(int(pid), os.WNOHANG)[0] != 0
        except (OSError, ValueError, IndexError):
            pass
    if reaped:
        log(f"runner: {reaped} ended processes from before its reloads cleared")


class Running:
    def __init__(self, job, live, source, wanted, shares=1024, limit_mb=None):
        self.job, self.live, self.source, self.wanted = job, live, source, wanted
        self.sources = {}  # each watched model's newest run as it started
        self.started = time.time()
        self.name = f"mg-{job.name}"
        folder = f"{WORK}/{job.name}"
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
        # MG_REGION: the European mirrors and partial reads (sources.py, omremote.py; 21.10.2026).
        command = ["docker", "run", "--rm", "--name", self.name, "--cpu-shares", str(shares), "-e", f"MG_JOB={job.name}",
                   "-e", f"MG_REGION={REGION}", "-e", f"MG_OPS={OPS_HOST}:{OPS_UDP}",
                   "-e", "PYTHONPATH=/code/runner/ops/hook",
                   # glibc's per-thread memory pools kept GBs the jobs had freed (the Mix: 5.1 GB process, 2.2 GB in its
                   # caches, 21.10.2026); two pools are enough for their few threads.
                   "-e", "MALLOC_ARENA_MAX=2",
                   # Its planned memory and limit (MB) into its usage samples (measure.py): the panel card's dashed lines.
                   "-e", f"MG_PLAN_MB={round(mem_need(job))}", "-e", f"MG_LIMIT_MB={round(limit_mb) if limit_mb else ''}",
                   "-v", f"{REPO}:/code", "-v", f"{folder}:/work", "-v", f"{STATS}:/stats",
                   # Out of memory, the kernel takes a job before WRF or the runner.
                   "--oom-score-adj", str(JOB_OOM_SCORE)]
        if CACHE and os.path.isdir(CACHE):
            command += ["-v", f"{CACHE}:/cache", "-e", "MG_CACHE=/cache"]
        if limit_mb:
            command += ["--memory", f"{int(limit_mb)}m"]
        if os.path.exists(ENV_FILE):
            command += ["--env-file", ENV_FILE]
        script = ["python", *job.args] + ([] if live else ["--local", "/work/out"])
        # The repo's measure.py (mounted at /code), so a change there needs no image rebuild (23.10.2026: its usage samples).
        command += [IMAGE, "python", "/code/runner/measure.py", job.name, "--", *script]
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

    def handed(self):
        """What the reloaded runner needs to take this run over (Adopted)."""
        return {"job": self.job.name, "pid": self.process.pid, "started": self.started, "live": self.live,
                "source": self.source, "wanted": self.wanted, "sources": self.sources, "log": self.log_path}


class Adopted(Running):
    """A run started before a reload, taken over by the reloaded runner (the owner, 27.10.2026: World ECMWF 12Z made and
    published on 26.10 but never recorded: the push of the runner's code at 19:28 UTC reloaded it mid-run, and the new
    runner only saw its container). os.execv keeps the runner's process, so its `docker run` is still our child: waitpid
    gives its exit code. Started some other way (the runner restarted), it isn't: its end is when its container goes,
    its exit code measure.py's record of the run."""

    def __init__(self, job, handed):
        self.job, self.live, self.source, self.wanted = job, handed["live"], handed["source"], handed["wanted"]
        self.sources, self.started, self.log_path = handed.get("sources") or {}, handed["started"], handed["log"]
        self.name, self.pid = f"mg-{job.name}", handed["pid"]
        self.ours = True

    def ended(self):
        if self.ours:
            try:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
                return None if pid == 0 else os.waitstatus_to_exitcode(status)
            except ChildProcessError:
                self.ours = False
        if self.job.name in containers():
            return None
        try:
            with open(f"{STATS}/{self.job.name}.jsonl") as f:
                for record in map(json.loads, reversed(f.readlines()[-20:])):
                    if record.get("start", 0) >= self.started - 120:
                        return record.get("exit")
        except (OSError, ValueError):
            pass
        return -1  # gone without a record: counted as failed

    def finished(self):
        code = self.ended()
        if code is None and time.time() - self.started > self.job.timeout * 60:
            log(f"{self.job.name}: over {self.job.timeout} min, stopped")
            subprocess.run(["docker", "kill", self.name], capture_output=True)
            code = None
            for _ in range(30):
                code = self.ended()
                if code is not None:
                    break
                time.sleep(1)
            code = 137 if code is None else code
        if code is None:
            return None
        shutil.rmtree(f"{WORK}/{self.job.name}", ignore_errors=True)
        return code

    def handed(self):
        return {"job": self.job.name, "pid": self.pid, "started": self.started, "live": self.live, "source": self.source,
                "wanted": self.wanted, "sources": self.sources, "log": self.log_path}


_net_last = [None]


def net_rates():
    """The machine's network in and out (MB/s) since the last call, from /proc/net/dev's real interfaces (eth*, en*,
    wl*: the containers' traffic goes through them too; docker0 and the veths would count it twice); None the first time
    (the owner, 22.10.2026: "on top lets show bandwidth utilization too")."""
    rx = tx = 0
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                name, data = line.split(":", 1)
                if name.strip().startswith(("eth", "en", "wl")):
                    values = data.split()
                    rx, tx = rx + int(values[0]), tx + int(values[8])
    except (OSError, ValueError, IndexError):
        return None, None
    now = time.time()
    last, _net_last[0] = _net_last[0], (now, rx, tx)
    if not last or now <= last[0] or rx < last[1] or tx < last[2]:
        return None, None
    dt = now - last[0]
    return round((rx - last[1]) / dt / 1e6, 2), round((tx - last[2]) / dt / 1e6, 2)


def sampler():
    """Every minute each container's CPU and memory, and the machine's load, free memory and network (MB/s)."""
    while True:
        try:
            out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], capture_output=True,
                                 text=True, timeout=50).stdout
            containers = [json.loads(line) for line in out.splitlines() if line.strip()]
            with open("/proc/meminfo") as f:
                mem = {line.split(":")[0]: int(line.split()[1]) for line in f}
            net_in, net_out = net_rates()
            append(f"{STATS}/_samples.jsonl", {
                "t": round(time.time()), "load": os.getloadavg()[0], "mem_free_mb": mem["MemAvailable"] // 1024,
                "net_in": net_in, "net_out": net_out, "wrf": wrf_running(),
                "containers": [{"name": c.get("Name"), "cpu": c.get("CPUPerc"), "mem": c.get("MemUsage")} for c in containers],
            })
        except Exception as error:
            log(f"sampler: {error}")
        time.sleep(60)


def trim_cache():
    """The packs' cache down to 90% of CACHE_GB when over it, least recently used first; half-written files over an hour
    old removed. Returns (GB kept, files removed)."""
    files, total, removed = [], 0, 0
    for folder, _, names in os.walk(CACHE):
        for name in names:
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if name.endswith(".tmp"):
                if time.time() - st.st_mtime > 3600:
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError:
                        pass
                continue
            files.append((st.st_mtime, st.st_size, path))
            total += st.st_size
    if total > CACHE_GB * 1e9:
        for _, size, path in sorted(files):
            if total <= CACHE_GB * 0.9e9:
                break
            try:
                os.remove(path)
                total -= size
                removed += 1
            except OSError:
                pass
    return round(total / 1e9, 1), removed


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
        out_running.append({"job": name, "priority": run.job.priority, "started": round(run.started), "due": round(run.wanted), "live": run.live,
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
        wanted = due(job, state, cache, now, job.name in live)
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
    if now - _queue_at[0] >= 60:
        _queue_at[0] = now
        append(f"{STATS}/_queue.jsonl", {"t": round(now), "run": len(running) + len(others), "slots": total_max, "wrf": bool(wrf),
                                         "bud": budget, "res": reserved,
                                         "wait": [[w["job"], why_kind(w["why"]), w["priority"]] for w in out_waiting]})
    save(LIVE, {"t": round(now), "host": os.uname().nodename, "wrf": wrf, "budget": budget, "reserved": reserved,
                "slots": total_max, "live": sorted(live), "beat": round(last_beat) or None,
                "running": out_running, "waiting": sorted(out_waiting, key=lambda w: w["priority"]), "next": out_next,
                "lags": {m: sorted(v)[len(v) // 2] for m, v in lags.items() if v}})


def main():
    for folder in (HOME, STATS, LOGS, WORK):
        os.makedirs(folder, exist_ok=True)
    state = load(STATE, {})
    # Whether each job's last run was live, for the states from before it was kept (23.10.2026): from the runs' record.
    try:
        with open(f"{STATS}/_runs.jsonl") as f:
            for record in map(json.loads, f.readlines()[-3000:]):
                if record.get("job") in state and "live" not in state[record["job"]]:
                    state[record["job"]]["_was_live"] = bool(record.get("live"))
        for mine in state.values():
            if "_was_live" in mine:
                mine["live"] = mine.pop("_was_live")
    except (OSError, ValueError):
        pass
    try:
        with open(f"{STATS}/_arrivals.jsonl") as f:
            _arrived.update((a["model"], a["run"]) for a in map(json.loads, f.readlines()[-2000:]))
    except (OSError, ValueError, KeyError):
        pass
    # When each waiting job became due, from the last LIVE before this start (the ageing counts from it: each reload,
    # every push of the runner's code, had started it again, so the priority-3 jobs never reached AGE_AFTER and the
    # seas never ran, 22.10.2026).
    for w in load(LIVE, {}).get("waiting", []):
        if w.get("job") and w.get("since"):
            _due_since.setdefault(w["job"], w["since"])
    running, last_pull, stamp, wrf_was, last_beat, beat_said, last_ops = {}, 0, code_stamp(), None, 0, False, 0
    # The runs the runner had before its reload, taken over so their ends are recorded.
    by_name = {j.name: j for j in JOBS}
    for handed in load(HANDOVER, []):
        if handed.get("job") in by_name:
            running[handed["job"]] = Adopted(by_name[handed["job"]], handed)
    reap_strays({run.pid for run in running.values()})
    if os.path.exists(HANDOVER):
        os.remove(HANDOVER)
        log(f"runner: took over {len(running)} running jobs: " + ", ".join(sorted(running)) if running else "runner: no running jobs to take over")
    threading.Thread(target=sampler, daemon=True).start()
    log(f"runner: {len(JOBS)} jobs, at most {MAX_TOTAL} at once ({MAX_HEAVY} heavy), work in {WORK}")
    while True:
        now = time.time()
        house = 0  # the housekeeping's seconds this minute
        if now - last_pull > 600:
            pull()
            last_pull = now
            if code_stamp() != stamp:
                log(f"the runner's code changed: reloading ({len(running)} running jobs go on in their containers, handed over)")
                save(HANDOVER, [run.handed() for run in running.values()])
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
            # The runs' logs a week (about 60 MB a day; the owner asked about the disks, 21.10.2026).
            for name in os.listdir(LOGS):
                path = f"{LOGS}/{name}"
                if name != "runner.log" and now - os.path.getmtime(path) > 7 * 86400:
                    os.remove(path)
            # The minute records about 8 days (the panel reads a day: its machine band, Capacity).
            for name in ("_samples.jsonl", "_queue.jsonl"):
                trim(f"{STATS}/{name}", 12000)
            for job in JOBS:
                if job.tiny:
                    trim(f"{STATS}/{job.name}.jsonl", 2000)
            trim(f"{STATS}/_tiny_runs.jsonl", 3000)
            # Each run's usage samples (measure.py, the run's popup in the panel) 4 days: a few KB a run.
            usage_dir = f"{STATS}/_usage"
            for name in os.listdir(usage_dir) if os.path.isdir(usage_dir) else []:
                try:
                    if now - os.path.getmtime(f"{usage_dir}/{name}") > 4 * 86400:
                        os.remove(f"{usage_dir}/{name}")
                except OSError:
                    pass
            # The packs' cache within its cap (packcache.py).
            try:
                os.makedirs(CACHE, exist_ok=True)
                kept, removed = trim_cache()
                if removed:
                    log(f"cache: {kept} GB kept, {removed} files removed")
            except OSError as error:
                log(f"cache: {error}")
            house = time.time() - now
        # Finished runs.
        for name, run in list(running.items()):
            code = run.finished()
            if code is None:
                continue
            del running[name]
            # Failures in a row (two leave the heartbeat's live list: GitHub runs the job meanwhile).
            mine = state.setdefault(name, {})
            mine["fails"] = 0 if code == 0 else mine.get("fails", 0) + 1
            # 137: killed (its memory limit, or the timeout); its next run goes without a limit (mem_limit).
            mine["exit"] = code
            save(STATE, state)
            if run.job.tiny:
                if code:
                    log(f"{name}: failed, exit {code} ({mine['fails']} in a row)")
                # Their own record (the panel's live feeds: the radar's runs, 26.10.2026), apart from _runs.jsonl.
                append(f"{STATS}/_tiny_runs.jsonl", {"job": name, "start": round(run.started), "end": round(time.time()),
                                                     "exit": code, "live": run.live})
                continue
            idle = code == 0 and made_nothing(run.log_path, time.time() - run.started)
            append(f"{STATS}/_runs.jsonl", {"job": name, "due": round(run.wanted), "start": round(run.started),
                                            "source": round(run.source) if run.source else None,
                                            **({"sources": {m: round(r) for m, r in run.sources.items()}} if run.sources else {}),
                                            "end": round(time.time()), "exit": code, "live": run.live,
                                            **({"idle": True} if idle else {})})
            log(f"{name}: done in {(time.time() - run.started) / 60:.1f} min, exit {code}" + (", nothing new" if idle else ""))
        # WRF started or ended: the running jobs' CPU share follows.
        wrf = wrf_running()
        _shape["wrf"] = wrf
        there = containers()
        if wrf != wrf_was:
            set_shares(there, wrf)
            if wrf_was is not None:
                log(f"WRF {'started' if wrf else 'ended'}: the jobs' CPU share {LOW_SHARES if wrf else 1024} (by priority)")
            wrf_was = wrf
        # Due runs, as many as allowed (containers left from before a reload count too).
        others = [name for name in there if name not in running]
        heavy_now = sum(1 for r in running.values() if r.job.heavy) + sum(1 for j in JOBS if j.name in others and j.heavy)
        # While WRF runs: all the slots, one big job (the owner, 21.10.2026: halving the slots left 14 jobs waiting,
        # spot points and rain now among them; the memory budget and the low CPU share keep WRF safe).
        cpu_now = cpu_busy()
        flex = not wrf and cpu_now is not None and cpu_now < FLEX_CPU
        total_max, heavy_max = (MAX_TOTAL, 1) if wrf else (MAX_FLEX if flex else MAX_TOTAL, MAX_HEAVY)
        flex_left = 1  # starts beyond MAX_TOTAL this minute
        names, live, cache = env_names(), live_jobs(), {}
        by_name = {j.name: j for j in JOBS}
        # A job in the live file whose variables are missing (jobs.py needs, live_needs) stays in shadow and off the
        # heartbeat's live list, so GitHub keeps publishing it (22.10.2026: the blend without KNMI_API_KEY would have
        # left HARM-NL out of the Auto map).
        held = {n for n in live if n in by_name and any(v not in names for v in by_name[n].needs + by_name[n].live_needs)}
        if held != _held_said:
            for name in sorted(held - _held_said):
                job = by_name[name]
                log(f"{name}: in the live file but kept in shadow: needs " + ", ".join(v for v in job.needs + job.live_needs if v not in names))
            _held_said.clear()
            _held_said.update(held)
        live -= held
        budget = mem_budget() - (WRF_MB if wrf else 0)
        # A running job counts its plan or what it uses now, whichever is bigger (a job without measured runs is
        # planned at a guess: mix-world at 1.5 GB used 4.0 during WRF, 21.10.2026).
        reserved = sum(max(mem_need(by_name[n]), used_mb(n)) for n in list(running) + others if n in by_name)
        # What the machine really has for a new job: free now, less what the jobs started in the last RAMP_S still have
        # to take to reach their usual peak (B).
        available = mem_available()
        if available is not None:
            available -= sum(max(mem_need(r.job) - used_mb(n), 0) for n, r in running.items() if now - r.started < RAMP_S)
        plan_cap = budget * (1 if wrf else OVERCOMMIT)
        for name in [n for n in _ids if n not in running and n not in others]:
            del _ids[name]
        waiting = None  # the priority of a due job left waiting for memory
        waiting_why = {}
        drain = None  # the job the machine is being drained for
        def prio(job):
            """Its priority, a 3 due over AGE_AFTER counting as a 2, any job due over 3 x AGE_AFTER as a 1 (23.10.2026: the
            seas, waiting since 02:27 UTC for a big job's place, lost each one to the hourly 1s: the Mix, the blend)."""
            waited = now - _due_since.get(job.name, now)
            if waited > 3 * AGE_AFTER:
                return 1
            return 2 if job.priority >= 3 and waited > AGE_AFTER else job.priority

        # Live jobs before shadow ones (the owner, 23.10.2026: "build live-first in the scheduler": with WRF on, the one
        # big job's place went to the shadow weather-mix-world while World GFS 06Z, World ECMWF 06Z and ICON-EU 09Z, live,
        # waited from ~09:40 to past 13:00 UTC, riders a run behind). Then the riders' first models first (the owner,
        # 21.10.2026: the 00Z/12Z peaks); within a priority the job waiting longest first (the owner, 22.10.2026: the
        # seas, aged to a 2 since 09:46 UTC, still hadn't run at 13:26: with 10-20 jobs waiting all day and one start a
        # minute beyond MAX_TOTAL, the order of jobs.py always gave it to the same jobs; the end of the list never had a
        # turn). Each skipped one with why.
        live_waiting = None  # a live job left waiting for memory: shadow jobs don't take that memory meanwhile
        tiny_names = {j.name for j in JOBS if j.tiny}
        for job in sorted(JOBS, key=lambda j: (j.name not in live, prio(j), _due_since.get(j.name, now))):
            busy = sum(1 for n in [*running, *others] if n not in tiny_names)
            if job.name in running or job.name in others:
                continue
            if job.tiny:
                pass
            elif busy >= total_max:
                waiting_why[job.name] = f"all {total_max} slots busy"
                continue
            if busy >= MAX_TOTAL and flex_left <= 0:
                waiting_why[job.name] = f"one more start a minute beyond {MAX_TOTAL} (a new job's load shows the minute after)"
                continue
            # A slot kept for a 1 or 2 that comes in next minute.
            if prio(job) >= 3 and busy >= total_max - 1:
                waiting_why[job.name] = "priority 3 waits for a free slot (one kept for 1s and 2s)"
                continue
            if any(n not in names for n in job.needs):
                continue
            wanted = due(job, state, cache, now, job.name in live)
            if wanted is None:
                continue
            # Big jobs at their limit (it showed as "starting" for minutes, 22.10.2026).
            if job.heavy and heavy_now >= heavy_max:
                waiting_why[job.name] = f"big jobs: {heavy_now} of {heavy_max} running"
                continue
            need = mem_need(job)
            # Short of real memory (B): only that holds the jobs behind and drains the machine (C); a plan over its cap
            # alone just waits.
            short = available is not None and available < need + MEM_MARGIN
            # A small job (SMALL_MB or less, mostly a few minutes) still goes when it fits: holding it back for a
            # bigger job waiting for memory left the spots' jobs queued behind it during WRF (21.10.2026); the drain
            # too holds only the big ones (22.10.2026: it held nine jobs behind the rain nowcast).
            if drain and need > SMALL_MB:
                waiting_why[job.name] = f"held: making room for {drain} (waiting over {DRAIN_AFTER // 60} min)"
                continue
            if waiting is not None and prio(job) > waiting and (need > SMALL_MB or short):
                waiting_why[job.name] = "a job of higher priority waits for memory"
                continue
            if live_waiting and job.name not in live and (need > SMALL_MB or short):
                waiting_why[job.name] = f"a live job waits for memory: {live_waiting} first"
                continue
            # Too big to fit beside WRF at all (the regional wind's 9.7 GB in 5 GB, 21.10.2026): waits for WRF to end,
            # never starts beside it, and holds nobody back meanwhile.
            if wrf and need > budget:
                waiting_why[job.name] = f"waits for WRF to end: needs {need / 1000:.1f} GB, {budget / 1000:.1f} for jobs while it runs"
                continue
            if busy and short:
                waiting_why[job.name] = (f"waits for memory: needs {need / 1000:.1f} GB and {MEM_MARGIN / 1000:.0f} spare, "
                                         f"{max(available, 0) / 1000:.1f} free")
                if waiting is None:
                    log(f"{job.name}: due, waiting for memory ({need} MB + {MEM_MARGIN}; {available} MB really free)")
                waiting = prio(job) if waiting is None else min(waiting, prio(job))
                if job.name in live and live_waiting is None and not job.tiny:
                    live_waiting = job.name
                # Only a live job drains the machine (23.10.2026: a shadow job's drain held the live ones back).
                if job.name in live and now - _due_since.get(job.name, now) > DRAIN_AFTER and drain is None and not job.tiny:
                    drain = job.name
                    waiting_why[job.name] += f"; nothing new starts until it fits"
                continue
            if busy and reserved + need > plan_cap and not job.tiny:
                waiting_why[job.name] = (f"waits for planned memory: needs {need / 1000:.1f} GB, "
                                         f"{max(plan_cap - reserved, 0) / 1000:.1f} of the plan left")
                continue
            reserved += need
            if available is not None:
                available -= need
            if busy >= MAX_TOTAL and not job.tiny:
                flex_left -= 1
            is_live = job.name in live
            mine = state.setdefault(job.name, {})
            limit = None if mine.get("exit") == 137 else mem_limit(job)
            if limit is None:
                log(f"{job.name}: its last run was killed (137): this one without a memory limit")
            running[job.name] = Running(job, is_live, wanted if job.watch else None, _due_since.pop(job.name, now),
                                        cpu_shares(job, wrf), limit)
            mine["started"] = now
            mine["live"] = is_live
            if job.inputs:
                # Its inputs' publishes as it starts: a newer one makes it due again (D).
                mine["inputs"] = {s: input_stamp(s) for s in job.inputs}
                ran = input_runs(job)
                if ran:
                    mine["sources"] = running[job.name].sources = ran
            if job.watch:
                mine["source"] = wanted
                # Every watched model's newest run as the job starts (it makes each model's newest it finds).
                mine["sources"] = {model: newest_run(model, cache)[0] for model, _ in job.watch if newest_run(model, cache)[0]}
                running[job.name].sources = mine["sources"]
            save(STATE, state)
            heavy_now += job.heavy
            if not job.tiny:
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
        # A minute's work over 60 s holds the new runs' detection (newest_run) that long: said, with the housekeeping's
        # share (23.10.2026: suspected for ICON-EU's late runs; it was the upload, latest_done).
        took = time.time() - now
        if took > 60:
            log(f"the loop's minute took {took:.0f} s (housekeeping {house:.0f} s)")
        time.sleep(60)


if __name__ == "__main__":
    main()
