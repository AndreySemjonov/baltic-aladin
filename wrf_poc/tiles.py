"""WRF's 1 km box as our map tiles (the owner, 17.10.2026: "add WRF 1 km to the app and view it
separately"): the 1 km boxes' output (wrfout_d03; Cape Town's Breede valley box d04 beside it, the 3 km box filling
the seam between them), its 10 m wind turned from the grid's
axes to east and north (axes.py: the Lambert grid's tilt measured from its own latitudes and longitudes), the gust from the
boundary layer (gust.py, from 20.10.2026; before, WSPD10MAX only), at zooms 6-9 through the
shared tiler (tiler.py) as tiles/wrf-<region>/. Inside the box it counts fully, fading over its
last 6 km (the app draws ECMWF's world under it). The app shows it as its own map, "WRF 1 km"
(admins only while it's a test).

    REGION=tarifa python wrf_poc/tiles.py work         upload (INGEST_TOKEN)
    REGION=tarifa python wrf_poc/tiles.py work --local DIR
    REGION=tarifa python wrf_poc/tiles.py work --coarse --local DIR    the 3 km box as tiles/wrf3-<region>/ (zooms 5-7)
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
from wrf_poc import axes  # noqa: E402
from wrf_poc import gust as gusts  # noqa: E402

NAMES = {"tarifa": "Tarifa", "capetown": "Cape Town", "tarifa-ec": "Tarifa from ECMWF"}
ZOOMS = range(6, 10)
COARSE_ZOOMS = range(5, 8)
FADE_CELLS = 6
EARTH_KM = 6371.0


def unit(lat, lon):
    """Points on the unit sphere (for the nearest cell by straight-line distance)."""
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def placer(lat, lon, with_distance=False):
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
    # Outside the grid past 1.5 cells (1.5 km for the 1 km boxes, 4.5 for the 3 km one).
    reach = 1.5 * float(np.median(np.hypot(xc, yc)))

    def place(tile_lon, tile_lat):
        distance, k = tree.query(unit(tile_lat.ravel(), tile_lon.ravel()))
        weight = np.clip(edge[k] / FADE_CELLS, 0, 1) * (distance * EARTH_KM < reach)
        dx = (tile_lon.ravel() - lon.ravel()[k]) * shrink.ravel()[k] * km_lat
        dy = (tile_lat.ravel() - lat.ravel()[k]) * km_lat
        col = np.clip(k % cols + inv[0][k] * dx + inv[1][k] * dy, 0, cols - 1)
        row = np.clip(k // cols + inv[2][k] * dx + inv[3][k] * dy, 0, rows - 1)
        out = col.reshape(tile_lon.shape), row.reshape(tile_lon.shape), weight.reshape(tile_lon.shape)
        return (*out, (distance * EARTH_KM).reshape(tile_lon.shape)) if with_distance else out

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


# Two 1 km boxes (Cape Town's main box and the Breede valley's, 02.11.2026) can't overlap: between them, within
# SEAM_KM of both, the 3 km box fills what their fading edges leave, so no strip of the world's model shows through.
SEAM_KM = 8


def composite(places):
    """`place` over several boxes: the 1 km ones (all but the last) and the 3 km one (the last) for their seam. Its
    "column" is every box's (col, row, weight, km away); the weight the strongest 1 km box's, or 1 in the seam."""
    def place(tile_lon, tile_lat):
        parts = [p(tile_lon, tile_lat) for p in places]
        fine = parts[:-1]
        seam = np.all([d <= SEAM_KM for (_, _, _, d) in fine], axis=0)
        return parts, None, np.maximum(np.max([w for (_, _, w, _) in fine], axis=0), seam)
    return place


def composite_sample(grids, parts, _row):
    """The boxes' values blended at a tile's pixels: each 1 km box by its weight, the 3 km box over the seam."""
    fine = parts[:-1]
    strongest = np.max([w for (_, _, w, _) in fine], axis=0)
    seam = np.all([d <= SEAM_KM for (_, _, _, d) in fine], axis=0)
    weights = [w for (_, _, w, _) in fine] + [seam * (1 - strongest)]
    total = np.sum(weights, axis=0)
    values = sum(w * bilinear(g, c, r) for g, (c, r, _, _), w in zip(grids, parts, weights))
    return np.where(total > 0, values / np.where(total > 0, total, 1), 0)


def box(path):
    """A box's grid and its hours: lat, lon, east, north, gust (m/s, the grid's axes turned to east and north) and the
    hours' stamps."""
    with Dataset(path) as data:
        lat = np.asarray(data["XLAT"][0], dtype=np.float64)
        lon = np.asarray(data["XLONG"][0], dtype=np.float64)
        u, v = np.asarray(data["U10"][:]), np.asarray(data["V10"][:])
        strongest = gusts.all_hours(data)
        stamps = ["".join(c.decode() for c in t) for t in data["Times"][:]]
    # The tilt from the grid itself (COSALPHA/SINALPHA turned Cape Town's the wrong way).
    east, north = axes.to_earth(u, v, *axes.grid_north(lat, lon))
    return {"lat": lat, "lon": lon, "east": east, "north": north, "gust": np.maximum(strongest, np.hypot(east, north)), "stamps": stamps}


def main():
    work = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "work")
    local = sys.argv[sys.argv.index("--local") + 1] if "--local" in sys.argv else None
    region = os.environ.get("REGION", "tarifa")
    env = dict(line.split("=", 1) for line in (work / "env").read_text().split() if "=" in line)
    run_time = datetime.strptime(env["CYCLE"], "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)
    if "--coarse" in sys.argv:
        # The 3 km box on its own as tiles/wrf3-<region>/ (the owner, 02.11.2026: "can we post our WRF 3km too? If we
        # have calculated it anyway"): every hour of the run (the 1 km boxes only by daylight), wider than them.
        fine, name, zooms, km = [box(sorted((work / "wrf").glob("wrfout_d02_*"))[0])], f"wrf3-{region}", COARSE_ZOOMS, 3
    else:
        # The 1 km boxes: d03, and Cape Town's valley box d04.
        fine = [box(paths[0]) for d in ("d03", "d04") if (paths := sorted((work / "wrf").glob(f"wrfout_{d}_*")))]
        name, zooms, km = f"wrf-{region}", ZOOMS, 1
    if not fine:
        raise SystemExit("No wrfout_d03: nothing to tile")
    stamps = fine[0]["stamps"]
    south, north_, west, east_ = (min(float(b["lat"].min()) for b in fine), max(float(b["lat"].max()) for b in fine),
                                  min(float(b["lon"].min()) for b in fine), max(float(b["lon"].max()) for b in fine))
    keys = tiler.tiles_in_box((south, north_, west, east_), zooms)
    if len(fine) == 1:
        boxes = fine
        placement, sample = tiler.Placement(keys, placer(fine[0]["lat"], fine[0]["lon"])), bilinear
    else:
        # The 3 km box for the seam, at the 1 km boxes' hours.
        coarse = box(sorted((work / "wrf").glob("wrfout_d02_*"))[0])
        at_hours = [coarse["stamps"].index(t) for t in stamps]
        coarse = {**coarse, **{k: coarse[k][at_hours] for k in ("east", "north", "gust")}}
        boxes = fine + [coarse]
        placement = tiler.Placement(keys, composite([placer(b["lat"], b["lon"], with_distance=True) for b in boxes]))
        sample = composite_sample
    run = tiler.Run(name, run_time, zooms=zooms, model=f"WRF {km} km {NAMES.get(region, region)} (test)",
                    credit="WRF (NCAR), our test run from NOAA's GFS", force=True, local=local)
    for hour, stamp in enumerate(stamps):
        when = int(datetime.strptime(stamp, "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        layer = (lambda name: boxes[0][name][hour]) if len(boxes) == 1 else (lambda name: tuple(b[name][hour] for b in boxes))
        run.put(when, tiler.pack(placement, sample, lambda at: tiler.wind_rgb(at(layer("east")), at(layer("north")), at(layer("gust")))))
    run.finish(box=[round(south, 3), round(north_, 3), round(west, 3), round(east_, 3)])
    print(f"{name}: {len(stamps)} hours, {len(fine)} {km} km box(es), {len(placement.parts)} tiles an hour", flush=True)


if __name__ == "__main__":
    main()
