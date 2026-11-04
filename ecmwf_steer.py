"""ECMWF's wind at 700 hPa (about 3 km up) for the whole world as tiles: tiles/ecmwf-steer at zoom 3, as
icon-eu-steer (iconeu_tiles.py). The app carries the forecast's rain along it between the hours past ICON-EU's
5 days and outside its area (the owner, 4.11.2026: "Long forecast rain not moving"; "make A"). ECMWF's open data
(0.25°, CC BY 4.0), its 00/12 UTC runs to 15 days: only u and v at 700 hPa, by byte range (about 1.1 MB a step,
85 steps: ~95 MB a run; the tiles a few MB).

    python ecmwf_steer.py [--force] [--local DIR] [--hours N]
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import ecmwf_map
import iconeu_tiles
import quality
import tiler

CREDIT = "ECMWF, IFS open data, CC BY 4.0: https://www.ecmwf.int/en/forecasts/datasets/open-data"
ZOOMS = range(3, 4)


def fetch(run, step):
    """{"u": message, "v": message} at 700 hPa for one step ({} when it can't be read)."""
    try:
        index = [json.loads(line) for line in ecmwf_map.get(ecmwf_map.file_url(run, step, "index")).decode().splitlines()
                 if line.strip()]
        messages = {}
        for entry in index:
            if entry.get("levtype") == "pl" and entry.get("levelist") == "700" and entry.get("param") in ("u", "v"):
                start, length = entry["_offset"], entry["_length"]
                messages[entry["param"]] = ecmwf_map.get(ecmwf_map.file_url(run, step, "grib2"), start, start + length - 1)
        return step, messages
    except Exception as error:
        print(f"ECMWF 700 hPa +{step} h: {error}", flush=True)
        return step, {}


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    run = ecmwf_map.newest_run()
    steer = tiler.Run("ecmwf-steer", run, ZOOMS, "ECMWF 0.25° (wind at 700 hPa)", CREDIT, "--force" in args, local)
    if not steer.wanted:
        print(f"ECMWF 700 hPa {run:%Y%m%d%H} already published")
        return
    steps = ecmwf_map.STEPS[:limit] if limit else ecmwf_map.STEPS
    start, started = int(run.timestamp()), time.time()
    placement = sample = None
    # Downloads side by side, decoded here one at a time (ecCodes isn't thread safe).
    with ThreadPoolExecutor(4) as pool:
        for step, messages in pool.map(lambda s: fetch(run, s), steps):
            if set(messages) != {"u", "v"}:
                continue
            fields, grid = ecmwf_map.decode(messages)
            if placement is None:
                # shortcut: the grid's -180/179.75 edge isn't wrapped (the 150 km edge fade leaves the date line
                # without wind aloft), fine until riders kite in Fiji.
                # ECMWF's columns start at 180° exactly (grid_tools shifts only those over 180: none placed).
                lon0 = grid["longitudeOfFirstGridPointInDegrees"]
                grid = dict(grid, longitudeOfFirstGridPointInDegrees=lon0 - 360 if lon0 >= 180 else lon0)
                placement, sample, _ = iconeu_tiles.grid_tools(grid, fields["u"].shape, ZOOMS)
            t = start + step * 3600
            east, north = fields["u"], fields["v"]
            if steer.checked("ECMWF", t, quality.steering(east, north)):
                steer.put(t, tiler.pack(placement, sample, lambda at: tiler.steer_rgb(at(east), at(north))))
    if not steer.frames:
        raise SystemExit(f"ECMWF 700 hPa {run:%Y%m%d%H}: nothing read")
    steer.finish()
    print(f"ECMWF 700 hPa {run:%Y%m%d%H}: done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up (ecCodes' exit can crash Python after the work is published, as ICON-EU's).
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
