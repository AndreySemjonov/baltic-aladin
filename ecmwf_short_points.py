"""ECMWF's 06 and 18 UTC runs at the spots (the owner, 18.10.2026: our ECMWF at the spots was the 00 or
12 UTC run while Open-Meteo's already had the 06/18 one, so they read apart by the runs alone). Those
runs are 90 hours; Open-Meteo's time series take them for those hours and keep the long run's after.
The same here: each spot's wind from its nearest O1280 point and its weather from the nearest land
point (spot_cells.py add_native, as world_tiles.py's 00/12 UTC runs), from Open-Meteo's open data
(data_spatial/ecmwf_ifs/, CC BY 4.0; ECMWF's, CC BY 4.0), written over points/ecmwf.json and
points/ecmwf-wx.json for the run's hours, the long run's later hours kept. The map's tiles stay the
00/12 UTC runs' (world_tiles.py ecmwf).

    python ecmwf_short_points.py [--force] [--local DIR]
"""
import os
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

import maplib
import spot_cells
import omremote
import spot_points
import world_tiles

# A short run counts once this many hours are there (0-90 hourly: 91 files).
COMPLETE = 91


def newest_short_run():
    """The newest 06/18 UTC run with its hours there, and its files {step: key}; None when the newest
    complete run is a long one (world_tiles.py has it)."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for back in range(4):
        run = start - timedelta(hours=6 * back)
        keys = [k for k in world_tiles.s3_list(world_tiles.ECMWF9_RUNS + run.strftime("%Y/%m/%d/%H%MZ/")) if k.endswith(".om")]
        if run.hour % 12 == 0:
            if len(keys) >= 145:
                return None, None
            continue
        if len(keys) >= COMPLETE:
            files = {}
            for key in keys:
                valid = datetime.strptime(key.rsplit("/", 1)[-1][:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
                files[int((valid - run).total_seconds() // 3600)] = key
            return run, files
    return None, None


def run_time(name):
    """A points file's run as a time: "20261018T06Z-9km" -> 2026-10-18 06 UTC."""
    try:
        return datetime.strptime(str(name)[:12], "%Y%m%dT%HZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch(key):
    # At home only the variables used, read in part (omremote.py; the owner, 21.10.2026: Oregon ~17 MB/s from home).
    if omremote.PARTIAL:
        return omremote.Prefetched(world_tiles.ECMWF9 + key, [theirs for _, theirs, _ in world_tiles.ECMWF9_FIELDS])
    handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
    handle.close()
    for attempt in range(4):
        try:
            request = urllib.request.Request(world_tiles.ECMWF9 + key, headers={"User-Agent": maplib.USER_AGENT})
            with urllib.request.urlopen(request, timeout=600) as reply, open(handle.name, "wb") as out:
                while block := reply.read(1 << 22):
                    out.write(block)
            return handle.name
        except OSError:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def main():
    import omfiles
    args = sys.argv[1:]
    force = "--force" in args
    local = args[args.index("--local") + 1] if "--local" in args else None
    run, files = newest_short_run()
    if run is None:
        print("ECMWF: the newest run is a long one (world_tiles.py)")
        return
    run_name = run.strftime("%Y%m%dT%HZ") + "-9km"
    before = spot_points.published("ecmwf")
    published = run_time(before.get("run")) if before else None
    if not force and not local and published is not None and published >= run:
        print(f"ECMWF {run_name}: the spots' file has this run or a newer one ({before.get('run')})")
        return
    # O1280's points (as world_tiles.ecmwf9_steps): 2560 Gaussian latitudes, row i 20 + 4i points.
    x, _ = np.polynomial.legendre.leggauss(2560)
    lats = np.degrees(np.arcsin(x))[::-1]
    counts = np.array([20 + 4 * i for i in range(1280)] + [20 + 4 * (2559 - i) for i in range(1280, 2560)])
    native_lat = np.repeat(lats, counts)
    native_lon = (np.concatenate([np.arange(n) * 360.0 / n for n in counts]) + 180) % 360 - 180
    cells = spot_cells.SpotCells("ecmwf", "ecmwf_ifs", weather="ecmwf-wx")
    wanted = {"10u", "10v", "gust", "2t", "rain", "tcc", "low", "mid", "high"}
    steps = sorted(files)
    started, previous = time.time(), 0
    print(f"ECMWF {run_name}: {len(steps)} hours", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for step, path in zip(steps, omremote.bounded_map(fetch, [files[s] for s in steps], 4, ahead=5)):
            reader = omremote.reader(path)
            values = {}
            for ours, theirs, add in world_tiles.ECMWF9_FIELDS:
                if ours not in wanted:
                    continue
                try:
                    v = reader.get_child_by_name(theirs).read_array((slice(None), slice(None)))[0]
                except Exception:
                    continue
                values[ours] = np.nan_to_num(np.asarray(v, dtype=np.float32)) + add
            reader.close()
            omremote.done(path)
            if "rain" in values:
                values["rate"] = np.maximum(values.pop("rain"), 0) / max(step - previous, 1)
            previous = step
            cells.add_native(int(run.timestamp()) + step * 3600, {"lat": native_lat, "lon": native_lon, "values": values})
            if step % 24 == 0:
                print(f"  +{step} h, {time.time() - started:.0f} s", flush=True)
    cells.publish(run_name, local and local + "/points", keep_after=True)
    print(f"ECMWF {run_name}: done in {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
