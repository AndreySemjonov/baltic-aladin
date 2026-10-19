"""WRF's 1 km box as our map tiles (the owner, 17.10.2026: "add WRF 1 km to the app and view it
separately"): the innermost domain's output (wrfout_d03), its 10 m wind turned from the grid's
axes to east and north (COSALPHA, SINALPHA: the Lambert grid is tilted), the gust the hour's
strongest 10 m wind (WSPD10MAX, nwp_diagnostics) and at least the wind, at zooms 6-9 through the
shared tiler (tiler.py) as tiles/wrf-<region>/. Inside the box it counts fully, fading over its
last 6 km (the app draws ECMWF's world under it). The app shows it as its own map, "WRF 1 km"
(admins only while it's a test).

    REGION=tarifa python wrf_poc/tiles.py work         upload (INGEST_TOKEN)
    REGION=tarifa python wrf_poc/tiles.py work --local DIR
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tiler  # noqa: E402

NAMES = {"tarifa": "Tarifa", "capetown": "Cape Town"}
ZOOMS = range(6, 10)
FADE_CELLS = 6
EARTH_KM = 6371.0


def unit(lat, lon):
    """Points on the unit sphere (for the nearest cell by straight-line distance)."""
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def placer(lat, lon):
    """`place(tile_lon, tile_lat)` for the tiler on WRF's grid (lat, lon: its cells' centres):
    each pixel's fractional column and row (the owner, 19.10.2026: the nearest cell left hard 1 km
    squares in the tiles that the app's smoothing couldn't hide; Windguru's map is the same 1 km
    grid drawn smoothly). From the nearest cell, the pixel's offset in km turned into columns and
    rows through the grid's local steps there (the Lambert grid is tilted and its cells about 1 km:
    locally a plain slanted grid). The weight: fully inside, fading over the last FADE_CELLS."""
    rows, cols = lat.shape
    tree = cKDTree(unit(lat.ravel(), lon.ravel()))
    r, c = np.indices(lat.shape)
    edge = np.minimum.reduce([r, c, rows - 1 - r, cols - 1 - c]).ravel()
    km_lat = np.radians(1) * EARTH_KM

    def steps(grid):
        """The grid's change per column and per row at every cell (one-sided at its edges)."""
        return np.gradient(grid, axis=1), np.gradient(grid, axis=0)

    (lat_c, lat_r), (lon_c, lon_r) = steps(lat), steps(lon)
    shrink = np.cos(np.radians(lat))
    # km east and north per column and per row, and that 2 x 2's inverse at every cell.
    xc, xr, yc, yr = lon_c * shrink * km_lat, lon_r * shrink * km_lat, lat_c * km_lat, lat_r * km_lat
    det = xc * yr - xr * yc
    inv = [(yr / det).ravel(), (-xr / det).ravel(), (-yc / det).ravel(), (xc / det).ravel()]

    def place(tile_lon, tile_lat):
        distance, k = tree.query(unit(tile_lat.ravel(), tile_lon.ravel()))
        weight = np.clip(edge[k] / FADE_CELLS, 0, 1) * (distance * EARTH_KM < 1.5)
        dx = (tile_lon.ravel() - lon.ravel()[k]) * shrink.ravel()[k] * km_lat
        dy = (tile_lat.ravel() - lat.ravel()[k]) * km_lat
        col = np.clip(k % cols + inv[0][k] * dx + inv[1][k] * dy, 0, cols - 1)
        row = np.clip(k // cols + inv[2][k] * dx + inv[3][k] * dy, 0, rows - 1)
        return col.reshape(tile_lon.shape), row.reshape(tile_lon.shape), weight.reshape(tile_lon.shape)

    return place


def bilinear(grid, col, row):
    """A grid's values at fractional columns and rows: the four cells round each, blended."""
    rows, cols = grid.shape
    c0 = np.clip(np.floor(col).astype(int), 0, cols - 2)
    r0 = np.clip(np.floor(row).astype(int), 0, rows - 2)
    tc, tr = col - c0, row - r0
    top = grid[r0, c0] * (1 - tc) + grid[r0, c0 + 1] * tc
    bottom = grid[r0 + 1, c0] * (1 - tc) + grid[r0 + 1, c0 + 1] * tc
    return top * (1 - tr) + bottom * tr


def main():
    work = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "work")
    local = sys.argv[sys.argv.index("--local") + 1] if "--local" in sys.argv else None
    region = os.environ.get("REGION", "tarifa")
    env = dict(line.split("=", 1) for line in (work / "env").read_text().split() if "=" in line)
    run_time = datetime.strptime(env["CYCLE"], "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)
    outputs = sorted((work / "wrf").glob("wrfout_d03_*"))
    if not outputs:
        raise SystemExit("No wrfout_d03: nothing to tile")
    with Dataset(outputs[0]) as data:
        lat = np.asarray(data["XLAT"][0], dtype=np.float64)
        lon = np.asarray(data["XLONG"][0], dtype=np.float64)
        cosa, sina = np.asarray(data["COSALPHA"][0]), np.asarray(data["SINALPHA"][0])
        u, v = np.asarray(data["U10"][:]), np.asarray(data["V10"][:])
        strongest = np.asarray(data["WSPD10MAX"][:]) if "WSPD10MAX" in data.variables else None
        stamps = ["".join(c.decode() for c in t) for t in data["Times"][:]]
    # The grid's axes to east and north (WRF's own rule for its rotated grids).
    east = u * cosa - v * sina
    north = v * cosa + u * sina
    speed = np.hypot(east, north)
    gust = speed if strongest is None else np.maximum(strongest, speed)

    keys = tiler.tiles_in_box((lat.min(), lat.max(), lon.min(), lon.max()), ZOOMS)
    placement = tiler.Placement(keys, placer(lat, lon))
    run = tiler.Run(f"wrf-{region}", run_time, zooms=ZOOMS, model=f"WRF 1 km {NAMES.get(region, region)} (test)",
                    credit="WRF (NCAR), our test run from NOAA's GFS", force=True, local=local)
    for hour, stamp in enumerate(stamps):
        when = int(datetime.strptime(stamp, "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        run.put(when, tiler.pack(placement, bilinear,
                                 lambda at: tiler.wind_rgb(at(east[hour]), at(north[hour]), at(gust[hour]))))
    run.finish(box=[round(float(lat.min()), 3), round(float(lat.max()), 3), round(float(lon.min()), 3), round(float(lon.max()), 3)])
    print(f"wrf-{region}: {len(stamps)} hours, {len(placement.parts)} tiles an hour", flush=True)


if __name__ == "__main__":
    main()
