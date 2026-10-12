"""MET Nordic's wind at its full 1 km over its whole area as tiles (docs/TILES_PLAN.md in the app's
repository, phase 2; the owner, 12.10.2026: every model at its full area, no detail lost): MET
Norway's hourly run (the Nordic countries and the Baltic), read from the run's own file every grid
cell (the regional blend reads every second), hourly for the first day then every 3 hours, through
the shared tiler into tiles/metnordic-1km at zooms 4-7 (zoom 7: about 0.7 km a pixel here), its
weight fading over the last 50 km to its grid's edge. The three fields are read side by side, 8
hours at a time, and each 8 made into tiles before the next.

    python metnordic_tiles.py [--force] [--local DIR] [--hours N]
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

import metnordic_map as mn
import regional_tiles
import tiler

ZOOMS = range(4, 8)
FADE_KM = 50
ROWS, COLS = 2321, 1796
BOX = (49.0, 74.0, -6.0, 42.0)
NAMES = (("speed", "wind_speed_10m"), ("direction", "wind_direction_10m"), ("gust", "wind_speed_of_gust"))
CREDIT = "MET Nordic, MET Norway (CC BY 4.0)"


def place(lon, lat):
    gx, gy = mn.lcc(lat, lon)
    col, row = (gx - mn.X0) / mn.STEP, (gy - mn.Y0) / mn.STEP
    inside = (col >= 0) & (row >= 0) & (col <= COLS - 1) & (row <= ROWS - 1)
    cells = np.minimum(np.minimum(col, COLS - 1 - col), np.minimum(row, ROWS - 1 - row))
    return col, row, np.where(inside, np.clip(cells * mn.STEP / 1000 / FADE_KM, 0, 1), 0)


def sample(values, col, row):
    c = np.clip(col, 0, COLS - 1.001)
    r = np.clip(row, 0, ROWS - 1.001)
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
    fc, fr = c - c0, r - r0
    top = values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc
    bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc
    return top * (1 - fr) + bottom * fr


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    def ask(name):
        # MET Norway's server answers late or with 500 now and then: three tries.
        for attempt in range(3):
            try:
                return mn.ascii_values(mn.get(f"{mn.SOURCE}.ascii?{name}", timeout=120), name)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(15)
    times = ask("time")
    run = ask("forecast_reference_time")[0]
    run_time = datetime.fromtimestamp(run, timezone.utc)
    out = tiler.Run("metnordic-1km", run_time, ZOOMS, "MET Nordic 1 km", CREDIT, "--force" in args, local)
    if not out.wanted:
        print(f"MET Nordic {run_time:%Y%m%dT%HZ} already published")
        return
    # That run's own file ("latest" moves on within the hour).
    own = mn.SOURCE.replace("_latest.nc", f"_{run_time:%Y%m%dT%HZ}.nc")
    try:
        mn.get(own + ".das")
        mn.SOURCE = own
    except Exception:
        print("its own file not found, reading the latest", flush=True)
    steps = regional_tiles.hours_wanted(times, run)
    if limit:
        steps = steps[:limit]
    regional_tiles.STRIDE = 1
    started = time.time()
    placement = tiler.Placement(tiler.tiles_in_box(BOX, ZOOMS), place)
    print(f"MET Nordic {run_time:%Y%m%dT%HZ}: {len(placement.parts)} tiles, {len(steps)} hours", flush=True)
    # Eight hours at a time (each field about 130 MB for 8 hours at every cell).
    for k in range(0, len(steps), 8):
        chunk = steps[k:k + 8]
        with ThreadPoolExecutor(3) as pool:
            fields = dict(zip([key for key, _ in NAMES],
                              pool.map(lambda pair: regional_tiles.fetch_hours(pair[1], chunk, ROWS, COLS), NAMES)))
        for s in chunk:
            speed, degrees = np.nan_to_num(fields["speed"][s]), np.radians(np.nan_to_num(fields["direction"][s]))
            east, north = -speed * np.sin(degrees), -speed * np.cos(degrees)
            gust = np.nan_to_num(fields["gust"][s])
            raw = fields["speed"][s]
            out.put(int(times[s]), tiler.pack(placement, sample, lambda at: tiler.wind_rgb(at(east), at(north), at(gust)),
                                              valid=lambda at: np.isfinite(at(raw))))
        print(f"  {len(out.frames)} hours, {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    out.finish()


if __name__ == "__main__":
    main()
