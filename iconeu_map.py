"""Wind map frames for the whole Baltic from ICON-EU 7 km (DWD open data).

DWD's ICON-EU runs every 3 hours; the 00, 06, 12 and 18 UTC runs go 120 hours ahead
(hourly to 78 h, then every 3 hours). DWD publishes each variable and hour as a
whole-Europe GRIB2 file of about 1.1 MB (0.0625 degree grid). This downloads 10 m
wind (U, V) and gusts (VMAX, maximum of the last hour) for the newest complete main
run, cuts out the Baltic, interpolates it to Web Mercator at about the model's
resolution and writes one PNG per hour, in the same format as the MET Nordic map:

    <out>/map.json
    <out>/<run>/<unix time>.png   R = wind speed m/s x 5, G = direction x 256/360,
                                  B = gust m/s x 5
    <out>/land.png                land fraction x 255 (0 = sea), same pixels

    python iconeu_map.py site/icon-eu              newest complete main run
    python iconeu_map.py site/icon-eu 2026093012   a given run
    python iconeu_map.py --check                   print the newest complete main run
"""
import bz2
import json
import math
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes
import numpy as np
from PIL import Image

from metnordic_map import EARTH_WEB, USER_AGENT, mercator_y

BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
# The Baltic Sea: Denmark to the Gulf of Bothnia and the Gulf of Finland.
SOUTH, NORTH, WEST, EAST = 53.5, 66.0, 9.0, 31.0
DEGREES_PER_PIXEL = 0.0625  # the model's grid, along the longitude
STEPS = list(range(0, 79)) + list(range(81, 121, 3))
VARIABLES = ("U_10M", "V_10M", "VMAX_10M")


def url(run, step, variable):
    return (f"{BASE}/{run[8:]}/{variable.lower()}/"
            f"icon-eu_europe_regular-lat-lon_single-level_{run}_{step:03d}_{variable}.grib2.bz2")


def request(address, method="GET"):
    return urllib.request.Request(address, method=method, headers={"User-Agent": USER_AGENT})


def exists(address):
    try:
        with urllib.request.urlopen(request(address, "HEAD"), timeout=60):
            return True
    except urllib.error.URLError:
        return False


def invariant_message(variable):
    """A time-invariant field (FR_LAND, FR_LAKE), published with each day's 00 UTC run."""
    folder = f"{BASE}/00/{variable.lower()}/"
    for attempt in range(3):
        try:
            listing = urllib.request.urlopen(request(folder), timeout=60).read().decode()
            name = re.search(rf'href="(icon-eu_europe_regular-lat-lon_time-invariant_\d{{10}}_{variable}\.grib2\.bz2)"', listing)
            if not name:
                raise SystemExit(f"no ICON-EU {variable} file online")
            with urllib.request.urlopen(request(folder + name.group(1)), timeout=120) as reply:
                return bz2.decompress(reply.read())
        except (urllib.error.URLError, OSError):
            if attempt == 2:
                raise


def newest_run():
    """The newest 00/06/12/18 UTC run whose last file is online (DWD keeps about a day)."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for back in range(5):
        run = (start - timedelta(hours=6 * back)).strftime("%Y%m%d%H")
        if exists(url(run, 120, "VMAX_10M")) and exists(url(run, 120, "V_10M")):
            return run
    raise SystemExit("no complete ICON-EU run online")


def output_grid():
    """Latitude/longitude of each output pixel centre (row 0 = north)."""
    x_west, x_east = EARTH_WEB * math.radians(WEST), EARTH_WEB * math.radians(EAST)
    y_south, y_north = float(mercator_y(SOUTH)), float(mercator_y(NORTH))
    width = round((EAST - WEST) / DEGREES_PER_PIXEL)
    height = round(width * (y_north - y_south) / (x_east - x_west))
    xs = x_west + (np.arange(width) + 0.5) * (x_east - x_west) / width
    ys = y_north - (np.arange(height) + 0.5) * (y_north - y_south) / height
    lon = np.degrees(xs / EARTH_WEB)
    lat = np.degrees(2 * np.arctan(np.exp(ys / EARTH_WEB)) - np.pi / 2)
    return np.meshgrid(lat, lon, indexing="ij"), width, height


def download(run, step, variable):
    """One GRIB2 message (downloads run in threads)."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request(url(run, step, variable)), timeout=120) as reply:
                return bz2.decompress(reply.read())
        except (urllib.error.URLError, OSError, EOFError):
            if attempt == 2:
                raise


def decode(data):
    """Grid description and values (ecCodes is not thread safe: main thread only)."""
    handle = eccodes.codes_new_from_message(data)
    try:
        ni, nj = eccodes.codes_get(handle, "Ni"), eccodes.codes_get(handle, "Nj")
        grid = {key: eccodes.codes_get(handle, key) for key in (
            "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
            "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees", "jScansPositively")}
        values = eccodes.codes_get_values(handle).reshape(nj, ni).astype(np.float32)
    finally:
        eccodes.codes_release(handle)
    return grid, values


class Sampler:
    """Bilinear interpolation from the model grid to the output pixels."""

    def __init__(self, grid, lat, lon):
        lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
        if lon0 > 180:
            lon0 -= 360
        dj = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
        fj = (lat - lat0) / dj
        fi = (lon - lon0) / grid["iDirectionIncrementInDegrees"]
        self.j0, self.i0 = np.floor(fj).astype(int), np.floor(fi).astype(int)
        self.tj, self.ti = (fj - self.j0).astype(np.float32), (fi - self.i0).astype(np.float32)
        # Only the rows and columns we need, so each field is cropped once.
        self.rows = slice(self.j0.min(), self.j0.max() + 2)
        self.cols = slice(self.i0.min(), self.i0.max() + 2)
        self.j0 -= self.rows.start
        self.i0 -= self.cols.start

    def __call__(self, values):
        v = values[self.rows, self.cols]
        a, b = v[self.j0, self.i0], v[self.j0, self.i0 + 1]
        c, d = v[self.j0 + 1, self.i0], v[self.j0 + 1, self.i0 + 1]
        top = a + (b - a) * self.ti
        bottom = c + (d - c) * self.ti
        return top + (bottom - top) * self.tj


def land_fraction(lat, lon):
    """Land fraction 0-1 (0 = sea) at each pixel, from ICON-EU's invariant fields. DWD counts
    lakes as water; here they count as land, so only the sea shows as sea. The long-range
    maps of the same box use it too."""
    grid, land_values = decode(invariant_message("FR_LAND"))
    _, lake_values = decode(invariant_message("FR_LAKE"))
    sampler = Sampler(grid, lat, lon)
    return np.clip(np.nan_to_num(sampler(land_values) + sampler(lake_values), nan=1.0), 0, 1)


def main():
    if sys.argv[1:] == ["--check"]:
        print(newest_run())
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/icon-eu")
    run = sys.argv[2] if len(sys.argv) > 2 else newest_run()
    run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    (lat, lon), width, height = output_grid()
    run_name = run_time.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)

    sampler = None
    frames = []

    def fetch(step):
        return step, [download(run, step, variable) for variable in VARIABLES]

    with ThreadPoolExecutor(max_workers=8) as pool:
        for step, messages in pool.map(fetch, STEPS):
            fields = [decode(message) for message in messages]
            if sampler is None:
                sampler = Sampler(fields[0][0], lat, lon)
            u, v, gust = (sampler(values) for _, values in fields)
            speed = np.hypot(u, v)
            direction = np.mod(np.degrees(np.arctan2(-u, -v)), 360)  # where the wind comes from
            gust = np.maximum(gust, speed)
            rgb = np.stack([np.clip(np.rint(speed * 5), 0, 255),
                            np.mod(np.rint(direction * 256 / 360), 256),
                            np.clip(np.rint(gust * 5), 0, 255)], axis=-1).astype(np.uint8)
            time = int((run_time + timedelta(hours=step)).timestamp())
            Image.fromarray(rgb, "RGB").save(folder / f"{time}.png", optimize=True)
            frames.append({"time": time, "file": f"{run_name}/{time}.png"})

    land = land_fraction(lat, lon)
    Image.fromarray(np.rint(land * 255).astype(np.uint8), "L").save(out / "land.png", optimize=True)

    manifest = {
        "model": "ICON-EU 7 km",
        "source": "Deutscher Wetterdienst (DWD), ICON-EU, CC BY 4.0: https://opendata.dwd.de/",
        "run": run_time.strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "projection": "Web Mercator (EPSG:3857); the image spans the bounds edge to edge",
        "bounds": {"south": SOUTH, "north": NORTH, "west": WEST, "east": EAST},
        "width": width, "height": height,
        "encoding": {"red": "wind speed m/s x 5", "green": "direction (from) degrees x 256/360",
                     "blue": "gust m/s x 5 (maximum of the last hour)"},
        "land": "land.png",
        "frames": frames,
    }
    (out / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"ICON-EU run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
