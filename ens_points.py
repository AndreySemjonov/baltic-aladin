"""The ensemble's wind at the spots (the owner, 18.10.2026: the spot page's Kite row and banner, the
Wind list's week of kite chances, the chart's likely range): ECMWF's ensemble (ENS: 50 members, 0.25°,
15 days; ECMWF open data, CC BY 4.0, from its own AWS mirror, only the 10 m wind and gust read by
byte range), each member's wind at each catalog spot's ECMWF point, as ens/<spot id>.json:

    {"model": "ECMWF ENS", "run": "2026-10-18T00:00Z", "members": 50, "times": [unix, ...],
     "speed": [[m/s x 10, one a member], ...], "direction": [[where it comes from, degrees / 2], ...],
     "gust": [[m/s x 10], ...]}          (null where a step has none: the gust at the run's start)

The app moves it round its own Mix (the members' middle onto the Mix's wind) and counts the kite
chance with the rider's own settings (wind from and the spot's good directions). Every 00 and 12
UTC run (to 360 hours: 3-hourly to 144, then 6-hourly); about 13 GB read.

    python ens_points.py [--force] [--local DIR] [--steps 0,3,6]
"""
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes as ec
import numpy as np

import spot_points
import world_tiles

# ECMWF's open data mirrors, the next one when one says "Slow Down" (AWS's did, all of it, 18.10.2026).
MIRRORS = ["https://storage.googleapis.com/ecmwf-open-data/", "https://data.ecmwf.int/forecasts/",
           "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/"]
PUBLISHED = "https://files.meteogust.com/ens/index.json"
STEPS = list(range(0, 145, 3)) + list(range(150, 361, 6))
PARAMS = ("10u", "10v", "10fg")
AGENT = "baltic-aladin ens_points (+https://github.com/AndreySemjonov/baltic-aladin)"


def url(run, step, ext):
    """The file's path under a mirror."""
    day, hour = run.strftime("%Y%m%d"), run.strftime("%H")
    return f"{day}/{hour}z/ifs/0p25/enfo/{day}{hour}0000-{step}h-enfo-ef.{ext}"


def get(address, start=None, length=None, tries=8):
    headers = {"User-Agent": AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{start + length - 1}"
    for attempt in range(tries):
        try:
            mirror = MIRRORS[attempt % len(MIRRORS)] if not address.startswith("http") else ""
            with urllib.request.urlopen(urllib.request.Request(mirror + address, headers=headers), timeout=120) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if attempt == tries - 1:
                raise
        except OSError:
            if attempt == tries - 1:
                raise
        # A mirror says "Slow Down" (503) to many reads at once: the next one, waiting longer each round.
        time.sleep(min(2 ** (attempt // len(MIRRORS)), 30) + random.random() * 2)


def newest_run():
    """The newest 00/12 UTC run with its last step (360 h) published."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 12 * 12, minute=0, second=0, microsecond=0)
    for back in range(4):
        run = start - timedelta(hours=12 * back)
        if get(url(run, STEPS[-1], "index"), 0, 200):
            return run
    raise SystemExit("No complete ENS run found")


def published_run():
    try:
        return json.loads(get(PUBLISHED + f"?t={int(time.time())}") or b"{}").get("run")
    except Exception:
        return None


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    steps = [int(s) for s in args[args.index("--steps") + 1].split(",")] if "--steps" in args else STEPS
    started = time.time()
    run = newest_run()
    run_text = run.strftime("%Y-%m-%dT%H:%MZ")
    if local is None and "--force" not in args and published_run() == run_text:
        print(f"ENS {run_text} already published")
        return
    # Each catalog spot's ECMWF point (its csPoints, as the spot page's ECMWF row), else the spot.
    spots = []
    for spot in spot_points.spots():
        point = (spot.get("csPoints") or {}).get("ecmwf_ifs")
        lat, lon = (float(point[0]), float(point[1])) if isinstance(point, list) and len(point) == 2 else (float(spot["lat"]), float(spot["lon"]))
        spots.append((spot["id"], lat, lon))
    # The 0.25° grid's nearest point (rows north to south from 90, columns east from 0).
    cells = np.array([(int(round((90 - lat) / 0.25)), int(round((lon % 360) / 0.25)) % 1440) for _, lat, lon in spots])
    print(f"ENS {run_text}: {len(spots)} spots, {len(steps)} steps", flush=True)
    # values[param][step] = array [members, spots]
    values = {p: {} for p in PARAMS}

    def one_step(step):
        index = get(url(run, step, "index"))
        if not index:
            return step, None
        wanted = []
        for line in index.decode().splitlines():
            d = json.loads(line)
            if d.get("param") in PARAMS and d.get("type") in ("pf", "cf"):
                wanted.append((d["param"], int(d.get("number", 0)), d["_offset"], d["_length"]))
        out = {p: {} for p in PARAMS}
        grib = url(run, step, "grib2")

        def message(job):
            param, number, offset, length = job
            gid = ec.codes_new_from_message(get(grib, offset, length))
            try:
                field = ec.codes_get_values(gid).reshape(721, 1440)
            finally:
                ec.codes_release(gid)
            return param, number, field[cells[:, 0], cells[:, 1]].astype(np.float32)
        with ThreadPoolExecutor(4) as inner:
            for param, number, at in inner.map(message, wanted):
                out[param][number] = at
        return step, out

    members = None
    # 2 steps at a time, 4 reads each (8 at once: 32 met "Slow Down", 18.10.2026).
    with ThreadPoolExecutor(2) as pool:
        for step, out in pool.map(one_step, steps):
            if out is None:
                print(f"  step {step}: none", flush=True)
                continue
            for p in PARAMS:
                if out[p]:
                    numbers = sorted(out[p])
                    members = members or numbers
                    values[p][step] = np.stack([out[p][n] for n in numbers])
            if step % 24 == 0:
                print(f"  step {step}: {len(out['10u'])} members, {time.time() - started:.0f} s", flush=True)
    good = [s for s in steps if s in values["10u"] and s in values["10v"]]
    if not good:
        raise SystemExit("ENS: no steps read")
    times = [int((run + timedelta(hours=s)).timestamp()) for s in good]
    total = 0
    for k, (spot_id, lat, lon) in enumerate(spots):
        speed, direction, gust = [], [], []
        for s in good:
            u, v = values["10u"][s][:, k], values["10v"][s][:, k]
            sp = np.hypot(u, v)
            speed.append([int(round(x * 10)) for x in sp])
            direction.append([int(round(((math.degrees(math.atan2(-a, -b)) + 360) % 360) / 2)) % 180 for a, b in zip(u, v)])
            g = values["10fg"].get(s)
            gust.append(None if g is None else [int(round(max(x, y) * 10)) for x, y in zip(g[:, k], sp)])
        body = json.dumps({"model": "ECMWF ENS", "credit": "ECMWF ensemble open data (CC BY 4.0)", "run": run_text,
                           "point": [lat, lon], "members": len(members or []), "times": times,
                           "speed": speed, "direction": direction, "gust": gust}, separators=(",", ":")).encode()
        total += len(body)
        if local:
            (local / "ens").mkdir(parents=True, exist_ok=True)
            (local / "ens" / f"{spot_id}.json").write_bytes(body)
        else:
            world_tiles.upload(f"ens/{spot_id}.json", body)
    index = json.dumps({"model": "ECMWF ENS", "run": run_text, "spots": [s[0] for s in spots], "steps": len(good),
                        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}, separators=(",", ":")).encode()
    if local:
        (local / "ens" / "index.json").write_bytes(index)
    else:
        world_tiles.upload("ens/index.json", index)
    print(f"ENS {run_text}: {len(spots)} spots, {len(good)} steps, {total / 1e6:.1f} MB, {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
