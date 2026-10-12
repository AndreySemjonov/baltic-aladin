"""ALADIN 2.3 km at its whole domain as tiles (docs/TILES_PLAN.md in the app's repository, phase 2;
the owner, 12.10.2026: every model at its full area): ČHMÚ's Lambert domain (central Europe, north
to about 57N at the Baltic), runs every 6 hours, through the shared tiler into tiles/aladin at
zooms 4-6, its weight fading over the last 50 km to the domain's edge.

    python aladin_tiles.py [--force] [--local DIR]
"""
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import aladin_map
import extract
import tiler

ZOOMS = range(4, 7)
FADE_KM = 50
# Round the domain (the tiles it doesn't reach are dropped).
BOX = (36.0, 60.0, -6.0, 36.0)
CREDIT = "ALADIN, ČHMÚ (Czech Hydrometeorological Institute), open data (CC BY 4.0)"


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    run = extract.newest_run(datetime.now(timezone.utc))
    if run is None:
        raise SystemExit("no complete ALADIN run online")
    run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    out = tiler.Run("aladin", run_time, ZOOMS, "ALADIN 2.3 km", CREDIT, "--force" in args, local)
    if not out.wanted:
        print(f"ALADIN {run} already published")
        return
    started = time.time()
    with tempfile.TemporaryDirectory() as folder:
        with ThreadPoolExecutor(4) as pool:
            files = dict(zip(extract.VARIABLES, pool.map(lambda v: extract.download(run, v, Path(folder)), extract.VARIABLES.values())))
        grid = aladin_map.grid_of(files["speed"])
        if not grid["jScansPositively"]:
            raise SystemExit("ALADIN grid rows run north to south; not handled")
        nx, ny = grid["Nx"], grid["Ny"]
        fields = {key: aladin_map.read(path, ((0, ny - 1), (0, nx - 1))) for key, path in files.items()}
    print(f"ALADIN {run}: {nx} x {ny}, read in {time.time() - started:.0f} s", flush=True)

    def place(lon, lat):
        col, row = aladin_map.lambert(lat, lon, grid)
        inside = (col >= 0) & (row >= 0) & (col <= nx - 1) & (row <= ny - 1)
        cells = np.minimum(np.minimum(col, nx - 1 - col), np.minimum(row, ny - 1 - row))
        return col, row, np.where(inside, np.clip(cells * grid["DxInMetres"] / 1000 / FADE_KM, 0, 1), 0)

    def sample(values, col, row):
        c = np.clip(col, 0, nx - 1.001)
        r = np.clip(row, 0, ny - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        top = values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc
        bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr

    placement = tiler.Placement(tiler.tiles_in_box(BOX, ZOOMS), place)
    print(f"ALADIN {run}: {len(placement.parts)} tiles", flush=True)
    for t in sorted(fields["speed"]):
        if t < run_time.timestamp() or any(t not in fields[key] for key in fields):
            continue
        speed, degrees = fields["speed"][t], np.radians(fields["direction"][t])
        # Through its parts, so 350 and 10 degrees don't average to 180.
        east, north = -speed * np.sin(degrees), -speed * np.cos(degrees)
        gust = np.hypot(fields["gust_u"][t], fields["gust_v"][t])
        out.put(t, tiler.pack(placement, sample, lambda at: tiler.wind_rgb(at(east), at(north), at(gust))))
    out.finish()
    print(f"ALADIN {run}: done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
