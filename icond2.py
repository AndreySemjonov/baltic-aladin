"""DWD's ICON-D2 (2.2 km, Germany, Benelux, Denmark, the Alps, western Poland and Czechia; open
data, CC BY 4.0: https://opendata.dwd.de/) for the regional tiles (regional_tiles.py), on its
regular latitude-longitude grid: the newest run whose hours are all out, its fields hour by hour
(one bzip2'd GRIB2 file each), and how much it counts at a place: 1 inside, falling to 0 over the
last 50 km to its area's edge and its corners without data.
"""
import bz2
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import eccodes
import numpy as np

BASE = "https://opendata.dwd.de/weather/nwp/icon-d2/grib/{hour:02d}/{var}/"
FILE = "icon-d2_germany_regular-lat-lon_single-level_{run}_{step:03d}_2d_{var}.grib2.bz2"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"
# The grid: rows from the south (43.18N), columns from 3.94W, every 0.02 degrees.
NI, NJ, LAT0, LON0, STEP = 1215, 746, 43.18, -3.94, 0.02
LAST = 48
FADE_KM = 50


def get(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply.read()


def newest_run(var="vmax_10m"):
    """The newest run (UTC datetime) with all its hours out, else None: every run's folder looked
    at (one that can't be read just skipped)."""
    complete = []
    for hour in range(0, 24, 3):
        try:
            listing = get(BASE.format(hour=hour, var=var), timeout=60).decode()
        except OSError:
            continue
        found = set(re.findall(r"single-level_(\d{10})_(\d{3})_2d", listing))
        complete += [run for run, step in found if step == f"{LAST:03d}"]
    if not complete:
        return None
    return datetime.strptime(max(complete), "%Y%m%d%H").replace(tzinfo=timezone.utc)


def download(run, var, step):
    """One hour's file, unzipped (GRIB2 bytes)."""
    name = run.strftime("%Y%m%d%H")
    url = BASE.format(hour=run.hour, var=var) + FILE.format(run=name, step=step, var=var)
    for attempt in range(3):
        try:
            return bz2.decompress(get(url))
        except OSError:
            if attempt == 2:
                raise


def decode(data):
    """A GRIB2 message's field (rows from the south), NaN where the model has none. ecCodes isn't
    safe on several threads at once: one at a time."""
    handle = eccodes.codes_new_from_message(data)
    try:
        values = eccodes.codes_get_values(handle).astype(np.float32)
        missing = eccodes.codes_get(handle, "missingValue")
    finally:
        eccodes.codes_release(handle)
    values[values == missing] = np.nan
    return values.reshape(NJ, NI)


def fields(run, var, steps):
    """The steps' fields: eight downloads at a time, decoded one after another."""
    with ThreadPoolExecutor(8) as pool:
        raw = list(pool.map(lambda s: download(run, var, s), steps))
    return {s: decode(data) for s, data in zip(steps, raw)}


def weight_grid(sample):
    """How much the model counts at each cell: its distance to the grid's edge or a cell without
    data, over FADE_KM (a chamfer distance, two passes, about 2 km a cell)."""
    valid = np.isfinite(sample)
    big = 10_000.0
    d = np.where(valid, big, 0.0).astype(np.float32)
    d[0, :] = d[-1, :] = 0
    d[:, 0] = d[:, -1] = 0
    # Forward and backward passes (rows then columns: vectorised along one axis).
    for _ in range(2):
        for j in range(1, NJ):
            d[j] = np.minimum(d[j], d[j - 1] + 1)
        for j in range(NJ - 2, -1, -1):
            d[j] = np.minimum(d[j], d[j + 1] + 1)
        for i in range(1, NI):
            d[:, i] = np.minimum(d[:, i], d[:, i - 1] + 1)
        for i in range(NI - 2, -1, -1):
            d[:, i] = np.minimum(d[:, i], d[:, i + 1] + 1)
    return np.clip(d * 2.0 / FADE_KM, 0, 1)


def place(lon, lat):
    """Fractional column and row on the grid of each point, and whether it lies on the grid."""
    col = (lon - LON0) / STEP
    row = (lat - LAT0) / STEP
    inside = (col >= 0) & (row >= 0) & (col <= NI - 1) & (row <= NJ - 1)
    return col, row, inside


def sample(grid, col, row):
    """Bilinear (NaN where a corner has no data), clamped at the edge."""
    c = np.clip(col, 0, NI - 1.001)
    r = np.clip(row, 0, NJ - 1.001)
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
    fc, fr = c - c0, r - r0
    top = grid[r0, c0] * (1 - fc) + grid[r0, c0 + 1] * fc
    bottom = grid[r0 + 1, c0] * (1 - fc) + grid[r0 + 1, c0 + 1] * fc
    return top * (1 - fr) + bottom * fr


def weather(run, times):
    """{unix time: {air (C), rh (0-1), wind (m/s), rain (mm/h), cloud (0-1)}} for the asked times
    within the run (its total precipitation summed from the start: each hour's from the hour
    before)."""
    start = int(run.timestamp())
    hours = sorted({round((t - start) / 3600) for t in times} & set(range(1, LAST + 1)))
    if not hours:
        return {}
    need = sorted(set(hours) | {h - 1 for h in hours})
    t2 = fields(run, "t_2m", hours)
    rh = fields(run, "relhum_2m", hours)
    u = fields(run, "u_10m", hours)
    v = fields(run, "v_10m", hours)
    cloud = fields(run, "clct", hours)
    total = fields(run, "tot_prec", need)
    out = {}
    for h in hours:
        out[start + h * 3600] = {"air": t2[h] - 273.15, "rh": np.clip(rh[h] / 100, 0, 1), "wind": np.hypot(u[h], v[h]),
                                 "rain": np.maximum(total[h] - total[h - 1], 0), "cloud": np.clip(cloud[h] / 100, 0, 1)}
    return out
