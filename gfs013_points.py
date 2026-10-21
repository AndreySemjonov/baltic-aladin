"""GFS at the spots from its 0.13° grid, as Open-Meteo's gfs_global answers (the owner, 18.10.2026: our GFS
at the spots' sea points read up to 2.5 m/s off Open-Meteo's; their wind comes from GFS's 0.13° grid,
ours from NOAA's 0.25° files, so the spot's first sea cell on theirs was often a land cell on ours).
Open-Meteo's open data (data_spatial/ncep_gfs013/, CC BY 4.0; GFS itself NOAA's, public domain): each
hour's file of the newest complete run, the spots' wind from their nearest cell and their weather from
the nearest land cell (spot_cells.py, add_regular), written as points/gfs.json and points/gfs-wx.json.
The map's GFS tiles stay NOAA's 0.25° (world_tiles.py gfs, which leaves the spots to this).

    python gfs013_points.py [--force] [--local DIR] [--hours N]
"""
import os
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

import maplib
import spot_cells
import omremote
import sources
import spot_points
import world_tiles

BUCKET = "https://openmeteo.s3.amazonaws.com/"
RUNS = "data_spatial/ncep_gfs013/"
# The 0.13° files hold the wind and the air only; the gusts come from the 0.25° grid (as Open-Meteo's
# gfs_global: world_tiles.py's points/gfs-025.json) and the weather stays world_tiles.py's (gfs-wx).
FIELDS = (("10u", "wind_u_component_10m", 0), ("10v", "wind_v_component_10m", 0), ("gust", "wind_gusts_10m", 0))
# A run counts once this many hours are there (Open-Meteo's GFS 0.13°: hourly to 120 h, then 3-hourly: 209).
COMPLETE = 200


def newest_run():
    """The newest run with its hours all there: (its time, {hour: key})."""
    for day_offset in range(0, 3):
        day = datetime.fromtimestamp(time.time() - day_offset * 86400, timezone.utc)
        folders = sorted({k.split("/")[5] for k in world_tiles.s3_list(f"{RUNS}{day:%Y/%m/%d}/") if k.endswith(".om")}, reverse=True)
        for folder in folders:
            run = datetime.strptime(f"{day:%Y%m%d}{folder}", "%Y%m%d%H%MZ").replace(tzinfo=timezone.utc)
            keys = [k for k in world_tiles.s3_list(f"{RUNS}{day:%Y/%m/%d}/{folder}/") if k.endswith(".om")]
            if len(keys) < COMPLETE:
                continue
            files = {}
            for key in keys:
                valid = datetime.strptime(key.rsplit("/", 1)[-1][:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
                files[int((valid - run).total_seconds() // 3600)] = key
            return run, files
    raise SystemExit("No complete GFS 0.13° run found")


def fetch(key):
    # At home only the variables used, read in part (omremote.py; the owner, 21.10.2026: Oregon ~17 MB/s from home).
    if omremote.PARTIAL:
        return omremote.Prefetched(sources.om_url(key), [theirs for _, theirs, _ in FIELDS])
    handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
    handle.close()
    for attempt in range(4):
        try:
            request = urllib.request.Request(sources.om_url(key), headers={"User-Agent": maplib.USER_AGENT})
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
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    run, files = newest_run()
    run_name = run.strftime("%Y%m%dT%HZ") + "-013"
    before = spot_points.published("gfs")
    if not force and not local and before and before.get("run") == run_name:
        print(f"GFS 0.13° {run_name}: the spots' file made already")
        return
    hours = sorted(files)[:limit] if limit else sorted(files)
    print(f"GFS 0.13° {run_name}: {len(hours)} hours", flush=True)
    cells = spot_cells.SpotCells("gfs", "gfs_global")
    started, previous, grid = time.time(), 0, None
    with ThreadPoolExecutor(max_workers=6) as pool:
        # Reads in part are many small requests: more files at once (omremote.py), a few ahead at most.
        for step, path in zip(hours, omremote.bounded_map(fetch, [files[h] for h in hours], 16 if omremote.PARTIAL else 6)):
            reader = omremote.reader(path)
            fields = {}
            for ours, theirs, add in FIELDS:
                try:
                    values = reader.get_child_by_name(theirs).read_array((slice(None), slice(None)))
                except Exception:
                    continue
                values = np.asarray(values, dtype=np.float32)
                fields[ours] = values[0] if values.ndim == 3 else values
                fields[ours] = fields[ours] + add
            reader.close()
            omremote.done(path)
            if grid is None and "10u" in fields:
                print("  fields:", ", ".join(sorted(fields)), "shape", fields["10u"].shape, flush=True)
                rows, cols = fields["10u"].shape
                # Its box (meta.json's BBOX): -89.912125..89.912125 north, -180 east, 3072 x 1536.
                grid = {"latitudeOfFirstGridPointInDegrees": -89.912125, "longitudeOfFirstGridPointInDegrees": -180.0,
                        "jDirectionIncrementInDegrees": 2 * 89.912125 / (rows - 1), "iDirectionIncrementInDegrees": 360.0 / cols,
                        "jScansPositively": 1}
            if grid is None:
                continue
            previous = step
            cells.add_regular(int(run.timestamp()) + step * 3600, grid, fields)
            if step % 24 == 0:
                print(f"  +{step} h, {time.time() - started:.0f} s", flush=True)
    # The gusts from the 0.25° grid at the same place and hour (at least the wind).
    coarse = spot_points.published("gfs-025") or {}
    times = {t: i for i, t in enumerate(coarse.get("times", []))}
    gusted = 0
    for t, row in cells.wind.items():
        i = times.get(t)
        for key, value in row.items():
            if value is None:
                continue
            other = (coarse.get("points", {}).get(key) or [None] * (i + 1 if i is not None else 0))
            g = other[i][2] if i is not None and i < len(other) and other[i] else None
            if g is not None:
                value[2] = round(max(g, value[2]), 2)
                gusted += 1
    print(f"  gusts from the 0.25° file ({coarse.get('run')}): {gusted} point-hours", flush=True)
    cells.publish(run_name, local and local + "/points")
    print(f"GFS 0.13° {run_name}: done in {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
