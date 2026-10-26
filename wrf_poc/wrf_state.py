"""A WRF run's state between the Mac mini and the Tower (the owner, 23.10.2026: "A: move both WRF runs to the Mac on 6
processes, the Tower's WRF as the fallback"). The Mac puts it at each stage and every 5 minutes while it works
(mac_wrf.sh): files.meteogust.com/runner/wrf-<region>.json, uploaded with INGEST_TOKEN. The Tower's tower.sh checks it
after its GFS wait and runs the cycle itself only when the Mac doesn't answer, failed or is stuck. Standard library only.

    python wrf_poc/wrf_state.py put tarifa wrf 2026-10-23T06Z    (the Mac; INGEST_TOKEN in the environment)
    python wrf_poc/wrf_state.py check tarifa                     (the Tower; exit 0 the Mac made it, 1 it's on it,
                                                                   2 run it here)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

UPLOAD = "https://data.meteogust.com/tiles/upload/runner/wrf-{region}.json"
READ = "https://files.meteogust.com/runner/wrf-{region}.json"
AGENT = {"User-Agent": "baltic-aladin wrf_state (https://github.com/AndreySemjonov/baltic-aladin)"}
STALE = 20 * 60        # no word for this long: the Mac is down or its run died
DONE_FOR = 5 * 3600    # a "done" this recent is the cycle the Tower is about to run (the runs are 12 h apart)
STUCK = 4 * 3600       # working this long: stuck (a 48 h run takes ~2.5 h on the Mac, 23.10.2026)
# The run's start, kept between the puts of one run (a new "waiting" starts a new run).
LOCAL = os.path.expanduser("~/wrf-logs/.state-{region}.json")
# The Mac's last runs, for the ops panel (the owner, 23.10.2026: the Mac's runs in it; mac_beat.py uploads them):
# each one's stage times ("at"), cycle and result, the newest last.
RUNS = os.path.expanduser("~/wrf-logs/.runs-{region}.json")
KEEP = 20


def history(region):
    try:
        with open(RUNS.format(region=region)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def put(region, stage, cycle=""):
    token = os.environ.get("INGEST_TOKEN", "").strip()
    if not token:
        sys.exit("INGEST_TOKEN is not set")
    now = round(time.time())
    path = LOCAL.format(region=region)
    try:
        with open(path) as f:
            mine = json.load(f)
    except (OSError, ValueError):
        mine = {}
    if stage == "waiting" or not mine.get("started"):
        mine = {"started": now}
    mine.update({"t": now, "host": os.uname().nodename, "stage": stage, **({"cycle": cycle} if cycle else {})})
    # When each stage began (the beat's repeats keep the first time).
    mine.setdefault("at", {}).setdefault(stage, now)
    with open(path, "w") as f:
        json.dump(mine, f)
    if stage in ("done", "failed"):
        runs = [r for r in history(region) if r.get("started") != mine["started"]] + [mine]
        with open(RUNS.format(region=region), "w") as f:
            json.dump(runs[-KEEP:], f)
    request = urllib.request.Request(UPLOAD.format(region=region), data=json.dumps(mine).encode(), method="PUT",
                                     headers={"Authorization": "Bearer " + token, **AGENT})
    with urllib.request.urlopen(request, timeout=30):
        pass


def check(region):
    """0: the Mac made this cycle; 1: it's on it (wait); 2: run it here; 3: not read (no internet or the file store
    down: tower.sh waits when it saw the Mac working). Says why on stdout."""
    now = time.time()
    try:
        request = urllib.request.Request(READ.format(region=region) + f"?t={int(now)}", headers=AGENT)
        with urllib.request.urlopen(request, timeout=30) as reply:
            state = json.loads(reply.read().decode())
    except urllib.error.HTTPError as error:
        print(f"no word from the Mac ({error.code})")
        return 3 if error.code >= 500 else 2
    except Exception as error:
        print(f"the Mac's state not read ({error})")
        return 3
    age, stage = now - state.get("t", 0), state.get("stage", "?")
    said = f"the Mac ({state.get('host', '?')}): {stage}, {state.get('cycle') or 'cycle not chosen yet'}, {age / 60:.0f} min ago"
    if stage == "done" and age < DONE_FOR:
        print(said)
        return 0
    if age > STALE:
        print(said + ": stale")
        return 2
    if stage == "failed":
        print(said)
        return 2
    if now - state.get("started", now) > STUCK:
        print(said + f": working for {(now - state['started']) / 3600:.1f} h, stuck")
        return 2
    print(said)
    return 1


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "put":
        put(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
    elif len(sys.argv) == 3 and sys.argv[1] == "check":
        sys.exit(check(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(64)
