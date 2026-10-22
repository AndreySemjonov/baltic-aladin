"""The chance of rain as tiles over Europe (the owner, 15.10.2026, "Map and data": the Chance map was
one Baltic picture): for each hour, the share of an ensemble's runs that bring 0.5 mm or more in
that hour, at each place the finer ensemble that reaches it, fading into the other over its edge:

1. MEPS (MET Norway, 30 members lagged over 6 hours, 2.5 km; read every third cell, 7.5 km): the
   Nordic countries and the Baltic, about 61 hours (CC BY 4.0)
2. ICON-EU-EPS (DWD, 40 members, Europe; CC BY 4.0) under it: hourly to 48 hours

Each ensemble's chance smoothed a little (the chance near a place, as rain_chance.py). Tiles at
zooms 3-6 (tiles/chance), grey: 0 = nothing known, 1 + chance x 254. Every 3 hours (MEPS updates
hourly, ICON-EU-EPS every 6).

    python chance_tiles.py [--local DIR] [--hours N]
"""
import bz2
import http.client
import os
import re
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

import eccodes
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

import metnordic_map as nordic
import rain_chance as meps
import tiler
import world_tiles
from waves_tiles import regular_grid

ZOOMS = range(3, 7)
THRESHOLD = meps.THRESHOLD
MEPS_STRIDE = 3
MEPS_CHUNK = 4
EPS_BASE = "https://opendata.dwd.de/weather/nwp/icon-eu-eps/grib/"
EPS_MEMBERS = 40
EPS_HOURS = 48
EPS_STEP = 0.1            # the ensemble's cells put on a 0.1 degree grid before the tiles
FADE_KM = 60
CREDIT = "MET Norway, MEPS ensemble (CC BY 4.0); Deutscher Wetterdienst, ICON-EU-EPS (CC BY 4.0)"
USER_AGENT = {"User-Agent": "MeteoGust chance (data.meteogust.com)"}


class Grid:
    """One ensemble's hour on its own grid: the chance, how much it counts (fading over its edge),
    and where a pixel lies on it."""

    def __init__(self, name, box, place, sample, chance, weight):
        self.name, self.box, self.place, self.sample = name, box, place, sample
        self.chance, self.weight = chance, weight

    def touches(self, lon, lat):
        south, north, west, east = self.box
        return lat.max() >= south and lat.min() <= north and lon.max() >= west and lon.min() <= east

    def at(self, lon, lat):
        col, row, inside = self.place(lon, lat)
        if not np.any(inside > 0):
            return None
        weight = inside * self.sample(self.weight, col, row)
        if not np.any(weight > 0.01):
            return None
        return np.clip(self.sample(self.chance, col, row), 0, 1), np.clip(weight, 0, 1)


def edge_fade(known, km_per_cell):
    """1 inside the known cells, falling to 0 over the last FADE_KM to their edge."""
    if not known.any():
        return np.zeros(known.shape, np.float32)
    distance = ndimage.distance_transform_edt(np.pad(known, 1))[1:-1, 1:-1]
    return np.clip(distance * km_per_cell / FADE_KM, 0, 1).astype(np.float32)


def get(url, timeout=120):
    """A file, tried 4 times with longer waits."""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=USER_AGENT), timeout=timeout) as reply:
                return reply.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(20 * (attempt + 1))


# MARK: MEPS

def meps_hours(limit=None):
    """MEPS's chance for each of its hours (the first `limit`): {time (end of the hour): Grid}; {}
    when it can't be read."""
    source = meps.SOURCE
    try:
        times = nordic.ascii_values(nordic.get(f"{source}.ascii?time", timeout=120), "time")
    except Exception as error:
        print(f"MEPS: not read ({error})", flush=True)
        return {}
    rows, cols = (meps.ROWS - 1) // MEPS_STRIDE + 1, (meps.COLUMNS - 1) // MEPS_STRIDE + 1
    _, sample = regular_grid(0, 0, 1, 1, rows, cols, False)

    def place(lon, lat):
        fi, fj = meps.grid_position(lat, lon)
        col, row = fi / MEPS_STRIDE, fj / MEPS_STRIDE
        inside = (col >= 0) & (col <= cols - 1) & (row >= 0) & (row <= rows - 1)
        return col, row, inside.astype(np.float32)

    def read(first, last):
        query = (f"{meps.VARIABLE}[{first}:1:{last}][0][0:1:{meps.MEMBERS - 1}]"
                 f"[0:{MEPS_STRIDE}:{meps.ROWS - 1}][0:{MEPS_STRIDE}:{meps.COLUMNS - 1}]")
        for attempt in range(5):
            try:
                raw = nordic.get(f"{source}.dods?" + urllib.request.quote(query, safe=":,"))
                values = nordic.dods_array(raw, (last - first + 1, meps.MEMBERS, rows, cols))
                return np.where(values > 1e30, np.nan, values)
            except (OSError, ValueError, http.client.IncompleteRead):
                if attempt == 4:
                    raise
                time.sleep(20 * (attempt + 1))

    # The grid's corners for the box the tiles look at.
    corner_lat, corner_lon = [], []
    for fi in (0, meps.COLUMNS - 1):
        for fj in (0, meps.ROWS - 1):
            lat, lon = meps_lonlat(fi, fj)
            corner_lat.append(lat)
            corner_lon.append(lon)
    box = (min(corner_lat) - 1, max(corner_lat) + 1, min(corner_lon) - 2, max(corner_lon) + 2)
    out, previous = {}, None
    began = time.time()
    for start in range(0, min(len(times), (limit + 1) if limit else len(times)), MEPS_CHUNK):
        end = min(start + MEPS_CHUNK - 1, len(times) - 1)
        try:
            chunk = read(start, end)
        except Exception as error:
            print(f"MEPS: hours {start}-{end} not read ({error})", flush=True)
            break
        for k in range(chunk.shape[0]):
            summed = chunk[k]
            if previous is not None:
                hour = summed - previous
                known = np.isfinite(hour)
                count = known.sum(axis=0)
                wet = (np.where(known, hour, 0) >= THRESHOLD).sum(axis=0)
                chance = np.where(count > 0, wet / np.maximum(count, 1), 0).astype(np.float32)
                chance = ndimage.gaussian_filter(chance, meps.SMOOTH_CELLS * 2 / MEPS_STRIDE, mode="nearest")
                weight = edge_fade(count > meps.MEMBERS // 2, 2.5 * MEPS_STRIDE)
                out[int(times[start + k])] = Grid("MEPS", box, place, sample, chance, weight)
            previous = summed
    print(f"MEPS: {len(out)} hours in {time.time() - began:.0f} s", flush=True)
    return out


def meps_lonlat(fi, fj):
    """A MEPS grid cell's latitude and longitude (the Lambert projection backwards)."""
    import math
    n = math.sin(meps.LAT0)
    f = math.cos(meps.LAT0) * math.tan(math.pi / 4 + meps.LAT0 / 2) ** n / n
    rho0 = meps.R * f / math.tan(math.pi / 4 + meps.LAT0 / 2) ** n
    x, y = meps.X0 + fi * meps.STEP, meps.Y0 + fj * meps.STEP
    rho = math.copysign(math.hypot(x, rho0 - y), n)
    theta = math.atan2(x, rho0 - y)
    lat = 2 * math.atan((meps.R * f / rho) ** (1 / n)) - math.pi / 2
    return math.degrees(lat), math.degrees(theta / n + meps.LON0)


# MARK: ICON-EU-EPS

def eps_run():
    """The newest ICON-EU-EPS run with all its first 48 hours: (YYYYMMDDHH, its hour folder) or None."""
    best = None
    for hour in ("00", "06", "12", "18"):
        try:
            listing = get(f"{EPS_BASE}{hour}/tot_prec/", timeout=60).decode("utf-8", "replace")
        except Exception:
            continue
        runs = set(re.findall(r"single-level_(\d{10})_(\d{3})_tot_prec", listing))
        for run in {r for r, _ in runs}:
            steps = {int(s) for r, s in runs if r == run}
            if all(s in steps for s in range(0, EPS_HOURS + 1)) and (best is None or run > best[0]):
                best = (run, hour)
    return best


def eps_members(url):
    """A step's file: every member's accumulated rain (members x cells)."""
    data = bz2.decompress(get(url))
    out = []
    # eccodes reads from a file: written out first (in the temp folder: at home the working folder is the repository,
    # mounted into every job; 22.10.2026).
    path = os.path.join(tempfile.gettempdir(), "eps-step.grib2")
    with open(path, "wb") as file:
        file.write(data)
    with open(path, "rb") as file:
        while True:
            handle = eccodes.codes_grib_new_from_file(file)
            if handle is None:
                break
            try:
                out.append((eccodes.codes_get(handle, "perturbationNumber"), eccodes.codes_get_values(handle).astype(np.float32)))
            finally:
                eccodes.codes_release(handle)
    out.sort(key=lambda m: m[0])
    return np.stack([values for _, values in out])


def eps_coordinate(run, hour, name):
    data = bz2.decompress(get(f"{EPS_BASE}{hour}/{name}/icon-eu-eps_europe_icosahedral_time-invariant_{run}_{name}.grib2.bz2"))
    path = os.path.join(tempfile.gettempdir(), f"eps-{name}.grib2")
    with open(path, "wb") as file:
        file.write(data)
    with open(path, "rb") as file:
        handle = eccodes.codes_grib_new_from_file(file)
        try:
            return eccodes.codes_get_values(handle).astype(np.float64)
        finally:
            eccodes.codes_release(handle)


def eps_hours(limit=None):
    """ICON-EU-EPS's chance for its first 48 hours: {time (end of the hour): Grid}; {} when none."""
    found = eps_run()
    if not found:
        print("ICON-EU-EPS: no complete run", flush=True)
        return {}
    run, hour = found
    began = time.time()
    clat, clon = eps_coordinate(run, hour, "clat"), eps_coordinate(run, hour, "clon")
    # The cells onto a regular grid (the 4 nearest, by distance), once for the run.
    south, north = np.floor(clat.min() * 10) / 10, np.ceil(clat.max() * 10) / 10
    west, east = np.floor(clon.min() * 10) / 10, np.ceil(clon.max() * 10) / 10
    lats = np.arange(south, north + 1e-6, EPS_STEP)
    lons = np.arange(west, east + 1e-6, EPS_STEP)
    glat, glon = np.meshgrid(lats, lons, indexing="ij")

    def xyz(lat, lon):
        la, lo = np.radians(lat), np.radians(lon)
        return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)

    tree = cKDTree(xyz(clat, clon))
    distance, index = tree.query(xyz(glat.ravel(), glon.ravel()), k=4)
    weights = 1 / np.maximum(distance, 1e-9)
    weights /= weights.sum(axis=1, keepdims=True)
    known = (distance[:, 0] < np.radians(0.15)).reshape(glat.shape)
    weight = edge_fade(known, EPS_STEP * 111 * np.cos(np.radians(55)))
    place, sample = regular_grid(lats[0], lons[0], EPS_STEP, EPS_STEP, len(lats), len(lons), False)
    box = (float(south), float(north), float(west), float(east))
    start = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    url = f"{EPS_BASE}{hour}/tot_prec/icon-eu-eps_europe_icosahedral_single-level_{run}_{{step:03d}}_tot_prec.grib2.bz2"
    out, previous = {}, eps_members(url.format(step=0))
    for step in range(1, (limit or EPS_HOURS) + 1):
        try:
            summed = eps_members(url.format(step=step))
        except Exception as error:
            print(f"ICON-EU-EPS +{step} h: not read ({error})", flush=True)
            break
        wet = (summed - previous >= THRESHOLD).mean(axis=0).astype(np.float32)
        previous = summed
        chance = (wet[index] * weights).sum(axis=1).reshape(glat.shape).astype(np.float32)
        out[int(start.timestamp()) + step * 3600] = Grid("ICON-EU-EPS", box, place, sample, np.where(known, chance, 0), weight)
    print(f"ICON-EU-EPS {run}: {len(out)} hours in {time.time() - began:.0f} s", flush=True)
    return out


# MARK: Tiles

def composite(grids, keys):
    """The hour's pack: each tile blended from the grids in order (each over the ones after it)."""
    tiles = {}
    for key in keys:
        lon, lat = tiler.tile_lonlat(*key)
        acc = np.zeros(lon.shape, np.float32)
        remaining = np.ones(lon.shape, np.float32)
        for grid in grids:
            if not grid.touches(lon, lat):
                continue
            got = grid.at(lon, lat)
            if got is None:
                continue
            chance, weight = got
            acc += weight * remaining * chance
            remaining *= 1 - weight
        total = 1 - remaining
        if not np.any(total > 0.01):
            continue
        grey = np.where(total > 0.01, np.clip(np.rint(1 + acc / np.maximum(total, 1e-6) * 254), 1, 255), 0)
        tiles[key] = world_tiles.png(grey.astype(np.uint8), "L")
    return world_tiles.encode(tiles, {z: tiler.SIZE for z in ZOOMS})


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    started = time.time()
    run_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    fine = meps_hours(limit)
    coarse = eps_hours(limit)
    now = int(run_time.timestamp())
    hours = sorted(t for t in set(fine) | set(coarse) if t > now - 3600)
    if limit:
        hours = hours[:limit]
    if not hours:
        raise SystemExit("no ensemble could be read")
    boxes = [g.box for g in list(fine.values())[:1] + list(coarse.values())[:1]]
    keys = sorted({key for box in boxes for key in tiler.tiles_in_box(box, ZOOMS)})
    out = tiler.Run("chance", run_time, ZOOMS, "MEPS and ICON-EU-EPS", CREDIT, True, local)
    for t in hours:
        grids = [g for g in (fine.get(t), coarse.get(t)) if g is not None]
        data = composite(grids, keys)
        out.report.count()
        out.put(t, data)
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%d.%m %H} UTC: {', '.join(g.name for g in grids)}, "
              f"{len(data) / 1e6:.1f} MB, {time.time() - started:.0f} s", flush=True)
    out.finish(encoding="grey: 0 nothing known, 1 + chance x 254 (the share of members with "
                        f"{THRESHOLD} mm or more in the hour ending at the frame's time)")


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up: their exit can crash Python after the work is published
    # ("double free or corruption", 18.10.2026), failing the run and the workflow's next step.
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
