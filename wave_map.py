"""Wave map frames over Lithuania, Latvia and Estonia from DWD's European wave model EWAM
(Deutscher Wetterdienst open data, CC BY 4.0).

EWAM runs at 00 and 12 UTC and goes 78 hours ahead, hourly, on a 0.1 x 0.05 degree grid
(about 5.5 km here). This downloads significant wave height (SWH), mean wave direction (MWD)
and mean wave period (TM10) for the newest complete run and writes one PNG per hour on the
water map's Web Mercator box (the app draws them through the fine coastline's sea only):

    <out>/map.json                  "kind": "waves"
    <out>/<run>/<unix time>.png     RGB, read as raw numbers:
        red   = wave height m x 40 + 1 (1-255); 0 = no wave data
        green = mean wave period s x 10
        blue  = mean wave direction (from) degrees x 256/360

Land cells take their nearest sea cell's values first, so the coast never mixes in nothing.

    python wave_map.py site/waves              newest complete run
    python wave_map.py site/waves 2026100500   a given run
    python wave_map.py --check                 print the newest complete run
"""
import bz2
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes
import numpy as np
from PIL import Image

import maplib
import metnordic_map

BASE = "https://opendata.dwd.de/weather/maritime/wave_models/ewam/grib"
SOUTH, NORTH, WEST, EAST = metnordic_map.SOUTH, metnordic_map.NORTH, metnordic_map.WEST, metnordic_map.EAST
KM_PER_PIXEL = 2.5
STEPS = list(range(0, 79))
VARIABLES = ("SWH", "MWD", "TM10")


def url(run, step, variable):
    return f"{BASE}/{run[8:]}/{variable.lower()}/EWAM_{variable}_{run}_{step:03d}.grib2.bz2"


def request(address, method="GET"):
    return urllib.request.Request(address, method=method, headers={"User-Agent": maplib.USER_AGENT})


def exists(address):
    try:
        with urllib.request.urlopen(request(address, "HEAD"), timeout=60):
            return True
    except urllib.error.URLError:
        return False


def newest_run():
    """The newest 00/12 UTC run whose last hour is online for every variable."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 12 * 12, minute=0, second=0, microsecond=0)
    for back in range(4):
        run = (start - timedelta(hours=12 * back)).strftime("%Y%m%d%H")
        if all(exists(url(run, STEPS[-1], variable)) for variable in VARIABLES):
            return run
    raise SystemExit("no complete EWAM run online")


def download(run, step, variable):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request(url(run, step, variable)), timeout=120) as reply:
                return bz2.decompress(reply.read())
        except (urllib.error.URLError, OSError, EOFError):
            if attempt == 2:
                raise


def decode(data):
    """Grid description and values, NaN where the model has no sea (ecCodes: main thread only)."""
    handle = eccodes.codes_new_from_message(data)
    try:
        grid = {key: eccodes.codes_get(handle, key) for key in (
            "Ni", "Nj", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
            "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees", "jScansPositively")}
        missing = eccodes.codes_get(handle, "missingValue")
        values = eccodes.codes_get_values(handle).astype(np.float32)
    finally:
        eccodes.codes_release(handle)
    values[values == missing] = np.nan
    return grid, values.reshape(grid["Nj"], grid["Ni"])


class Cropped:
    """The EWAM rows and columns around the box, with land cells filled from the nearest sea
    cell, and a bilinear sampler to the output pixels."""

    def __init__(self, grid, lat, lon):
        from scipy import ndimage
        lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
        if lon0 >= 180:
            lon0 -= 360
        dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
        dlon = grid["iDirectionIncrementInDegrees"]
        fj, fi = (lat - lat0) / dlat, (lon - lon0) / dlon
        self.rows = slice(max(int(np.floor(fj.min())) - 2, 0), int(np.ceil(fj.max())) + 3)
        self.cols = slice(max(int(np.floor(fi.min())) - 2, 0), int(np.ceil(fi.max())) + 3)
        rows = self.rows.stop - self.rows.start
        cols = self.cols.stop - self.cols.start
        self.sampler = maplib.Bilinear(fi - self.cols.start, fj - self.rows.start, cols, rows)
        self.ndimage = ndimage
        self.fill = None

    def __call__(self, values):
        crop = values[self.rows, self.cols]
        if self.fill is None:
            sea = np.isfinite(crop)
            _, self.fill = self.ndimage.distance_transform_edt(~sea, return_indices=True)
        return crop[self.fill[0], self.fill[1]]

    def sample(self, values):
        return self.sampler(values)


def main():
    if sys.argv[1:] == ["--check"]:
        print(newest_run())
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/waves")
    run = sys.argv[2] if len(sys.argv) > 2 else newest_run()
    run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, KM_PER_PIXEL)
    run_name = run_time.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)

    crop = None
    frames = []
    highest = 0.0

    def fetch(step):
        return step, [download(run, step, variable) for variable in VARIABLES]

    with ThreadPoolExecutor(max_workers=8) as pool:
        for step, messages in pool.map(fetch, STEPS):
            fields = [decode(message) for message in messages]
            if crop is None:
                crop = Cropped(fields[0][0], lat, lon)
            height_m, direction, period = (crop(values) for _, values in fields)
            # Directions blend as their sine and cosine, so 350 and 10 make 0, not 180.
            radians = np.radians(direction)
            east, north = crop.sample(np.sin(radians)), crop.sample(np.cos(radians))
            direction_out = np.mod(np.degrees(np.arctan2(east, north)), 360)
            height_out = np.clip(crop.sample(height_m), 0, None)
            period_out = np.clip(crop.sample(period), 0, None)
            valid = crop.sampler.valid & np.isfinite(height_out)
            red = np.where(valid, np.clip(np.rint(np.nan_to_num(height_out) * 40) + 1, 1, 255), 0)
            rgb = np.stack([red,
                            np.clip(np.rint(np.nan_to_num(period_out) * 10), 0, 255),
                            np.mod(np.rint(np.nan_to_num(direction_out) * 256 / 360), 256)], axis=-1).astype(np.uint8)
            time = int((run_time + timedelta(hours=step)).timestamp())
            Image.fromarray(rgb, "RGB").save(folder / f"{time}.png", optimize=True)
            top = float(np.nanmax(np.where(valid, height_out, np.nan))) if valid.any() else 0.0
            highest = max(highest, top)
            frames.append({"time": time, "file": f"{run_name}/{time}.png", "heightMax": round(top, 2)})

    manifest = {
        "model": "EWAM 5 km",
        "kind": "waves",
        "source": "Deutscher Wetterdienst (DWD), EWAM, CC BY 4.0: https://opendata.dwd.de/",
        "run": run_time.strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "projection": "Web Mercator (EPSG:3857); the image spans the bounds edge to edge",
        "bounds": {"south": SOUTH, "north": NORTH, "west": WEST, "east": EAST},
        "width": width, "height": height,
        "landFilled": True,
        "heightMax": round(highest, 2),
        "encoding": {"red": "significant wave height m x 40 + 1 (0 = no data)",
                     "green": "mean wave period s x 10",
                     "blue": "mean wave direction (from) degrees x 256/360"},
        "frames": frames,
    }
    (out / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"EWAM run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB, highest {highest:.1f} m")


if __name__ == "__main__":
    main()
