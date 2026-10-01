"""Wind map frames for the whole Baltic from ECMWF's IFS open data (0.25°, CC BY 4.0), 15 days.

ECMWF publishes each step of its 00 and 12 UTC runs as one global GRIB2 file with an
index; this downloads only the three fields we need (10 m u and v and the gust, about
3 MB per step) by byte range: every 3 hours to 144 hours, then every 6 hours to 360.
Same box and pixels as the GFS map, land mask from ICON-EU; format in maplib.py.

    python ecmwf_map.py site/ecmwf          newest complete run
    python ecmwf_map.py --check             print the newest complete run (YYYYMMDDHH)
"""
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes as ec
import numpy as np

import gfs_map
import iconeu_map
import maplib

BASE = "https://data.ecmwf.int/forecasts"
STEPS = list(range(0, 145, 3)) + list(range(150, 361, 6))
PARAMS = ("10u", "10v", "10fg")


def file_url(run, step, suffix):
    return (f"{BASE}/{run.strftime('%Y%m%d')}/{run.strftime('%H')}z/ifs/0p25/oper/"
            f"{run.strftime('%Y%m%d%H')}0000-{step}h-oper-fc.{suffix}")


def get(url, start=None, end=None):
    headers = {"User-Agent": maplib.USER_AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=180) as reply:
                return reply.read()
        except OSError:
            if attempt == 2:
                raise


def newest_run():
    """The newest 00/12 UTC run (the ones that go 15 days) with its last step published."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 12 * 12, minute=0, second=0, microsecond=0)
    for back in range(4):
        run = start - timedelta(hours=12 * back)
        try:
            get(file_url(run, STEPS[-1], "index"))
            return run
        except urllib.error.URLError:
            continue
    raise SystemExit("No complete ECMWF run found")


def fetch(run, step):
    """{param: GRIB message} for one step (downloads only; run in threads)."""
    entries = [json.loads(line) for line in get(file_url(run, step, "index")).decode().splitlines() if line.strip()]
    messages = {}
    for entry in entries:
        if entry.get("levtype") == "sfc" and entry.get("param") in PARAMS:
            start, length = entry["_offset"], entry["_length"]
            messages[entry["param"]] = get(file_url(run, step, "grib2"), start, start + length - 1)
    return step, messages


def decode(messages):
    """{param: global values} and the grid (ecCodes is not thread safe: main thread only)."""
    fields, grid = {}, None
    for param, message in messages.items():
        gid = ec.codes_new_from_message(message)
        try:
            grid = grid or {key: ec.codes_get(gid, key) for key in (
                "Ni", "Nj", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees", "jScansPositively")}
            fields[param] = ec.codes_get_values(gid).reshape(grid["Nj"], grid["Ni"])
        finally:
            ec.codes_release(gid)
    return fields, grid


def main():
    if sys.argv[1:] == ["--check"]:
        print(newest_run().strftime("%Y%m%d%H"))
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/ecmwf")
    run = newest_run()
    bounds = (gfs_map.SOUTH, gfs_map.NORTH, gfs_map.WEST, gfs_map.EAST)
    (lat, lon), width, height = maplib.output_grid(*bounds, gfs_map.KM_PER_PIXEL)
    out.mkdir(parents=True, exist_ok=True)
    maplib.save_land(out / "land.png", iconeu_map.land_fraction(lat, lon))
    run_name = run.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames, sample = [], None
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloads = pool.map(lambda step: fetch(run, step), STEPS)
        results = [(step, *decode(messages)) for step, messages in downloads]
    for step, fields, grid in results:
        if "10u" not in fields or "10v" not in fields:
            continue
        if sample is None:
            dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
            sample = maplib.regular_sampler(lat, lon, grid["latitudeOfFirstGridPointInDegrees"],
                                            grid["longitudeOfFirstGridPointInDegrees"], dlat,
                                            grid["iDirectionIncrementInDegrees"], grid["Ni"], grid["Nj"])
        u, v = sample(fields["10u"]), sample(fields["10v"])
        speed = np.hypot(u, v)
        gust = sample(fields["10fg"]) if "10fg" in fields else speed
        t = int((run + timedelta(hours=step)).timestamp())
        maplib.save_frame(folder / f"{t}.png", speed, maplib.direction_from(u, v), gust)
        frames.append({"time": t, "file": f"{run_name}/{t}.png"})
    manifest = maplib.write_manifest(out, "ECMWF 0.25°", "ECMWF, IFS open data, CC BY 4.0: https://www.ecmwf.int/en/forecasts/datasets/open-data",
                                     run, bounds, width, height, frames,
                                     gust_note="gust m/s x 5 (maximum since the previous step)")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"ECMWF run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
