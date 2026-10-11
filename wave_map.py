"""Wave map frames over Lithuania, Latvia and Estonia: the Copernicus Baltic wave forecast
(BALTICSEA_ANALYSISFORECAST_WAV_003_010, WAM at 1 nautical mile, hourly, up to 10 days; E.U.
Copernicus Marine Service Information; the owner, 11.10.2026: EWAM's 3 days were too few), hourly
for 3 days and every 3 hours after; when it can't be read (no login, late), DWD's European wave
model EWAM as before (Deutscher Wetterdienst open data, CC BY 4.0: 00 and 12 UTC, 78 hours
ahead, hourly, 0.1 x 0.05 degrees, about 5.5 km). Significant wave height, mean wave direction
and mean wave period (TM10), one PNG per frame on the water map's Web Mercator box (the app draws
them through the fine coastline's sea only):

    <out>/map.json                  "kind": "waves"
    <out>/<run>/<unix time>.png     RGB, read as raw numbers:
        red   = wave height m x 40 + 1 (1-255); 0 = no wave data
        green = mean wave period s x 10
        blue  = mean wave direction (from) degrees x 256/360

Land cells take their nearest sea cell's values first, so the coast never mixes in nothing.

    python wave_map.py site/waves              Copernicus, else EWAM's newest complete run
    python wave_map.py site/waves 2026100500   EWAM, a given run
    python wave_map.py --check                 print the run to show (Copernicus: the half day)
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

import copernicus
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


def copernicus_run(now=None):
    """The Copernicus run to show: its runs are online by about 10 hours after 00 and 12 UTC, so
    the half day 10 hours ago (the map is made again twice a day, at 10 and 22 UTC)."""
    now = now or datetime.now(timezone.utc)
    slot = now - timedelta(hours=10)
    return slot.replace(hour=slot.hour // 12 * 12, minute=0, second=0, microsecond=0)


def copernicus_frames(lat, lon):
    """The Copernicus frames from 3 hours ago: hourly for 3 days, then every 3 hours as far as it
    goes. Each: (unix time, height, period, direction, valid) on the output pixels; None when it
    can't be read or has under 3 days."""
    from scipy import ndimage
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    times = [start + timedelta(hours=h) for h in range(0, 75)] + [start + timedelta(hours=h) for h in range(75, 243, 3)]
    data = copernicus.load_waves(SOUTH - 0.2, NORTH + 0.2, WEST - 0.3, EAST + 0.3, times)
    if data is None or len(data["times"]) < 72:
        return None
    lats, lons = data["lats"], data["lons"]
    sampler = maplib.regular_sampler(lat, lon, float(lats[0]), float(lons[0]), float(lats[1] - lats[0]),
                                     float(lons[1] - lons[0]), len(lons), len(lats))
    # Land takes the nearest sea cell's values (the land mask is the same every hour).
    sea = np.isfinite(data["height"][0])
    _, fill = ndimage.distance_transform_edt(~sea, return_indices=True)
    out = []
    for k, time in enumerate(data["times"]):
        height_m, direction, period = (data[name][k][fill[0], fill[1]] for name in ("height", "direction", "period"))
        radians = np.radians(direction)
        east, north = sampler(np.sin(radians)), sampler(np.cos(radians))
        out.append((time, np.clip(sampler(height_m), 0, None), np.clip(sampler(period), 0, None),
                    np.mod(np.degrees(np.arctan2(east, north)), 360), sampler.valid))
    return out


def ewam_frames(run, run_time, lat, lon):
    """EWAM's frames of a run, hourly for its 78 hours: as copernicus_frames."""
    crop = None
    out = []

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
            time = int((run_time + timedelta(hours=step)).timestamp())
            out.append((time, np.clip(crop.sample(height_m), 0, None), np.clip(crop.sample(period), 0, None),
                        np.mod(np.degrees(np.arctan2(east, north)), 360), crop.sampler.valid))
    return out


def main():
    if sys.argv[1:] == ["--check"]:
        print(copernicus_run().strftime("%Y%m%d%H") if copernicus.available() else newest_run())
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/waves")
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, KM_PER_PIXEL)
    frames_in = None
    if len(sys.argv) <= 2:
        frames_in = copernicus_frames(lat, lon)
    if frames_in is not None:
        run_time = copernicus_run()
        model = "Baltic WAM 2 km"
        source = "E.U. Copernicus Marine Service Information, BALTICSEA_ANALYSISFORECAST_WAV_003_010: https://marine.copernicus.eu/"
    else:
        run = sys.argv[2] if len(sys.argv) > 2 else newest_run()
        run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        frames_in = ewam_frames(run, run_time, lat, lon)
        model = "EWAM 5 km"
        source = "Deutscher Wetterdienst (DWD), EWAM, CC BY 4.0: https://opendata.dwd.de/"
    run_name = run_time.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)

    frames = []
    highest = 0.0
    for time, height_out, period_out, direction_out, inside in frames_in:
        valid = inside & np.isfinite(height_out)
        red = np.where(valid, np.clip(np.rint(np.nan_to_num(height_out) * 40) + 1, 1, 255), 0)
        rgb = np.stack([red,
                        np.clip(np.rint(np.nan_to_num(period_out) * 10), 0, 255),
                        np.mod(np.rint(np.nan_to_num(direction_out) * 256 / 360), 256)], axis=-1).astype(np.uint8)
        Image.fromarray(rgb, "RGB").save(folder / f"{time}.png", optimize=True)
        top = float(np.nanmax(np.where(valid, height_out, np.nan))) if valid.any() else 0.0
        highest = max(highest, top)
        frames.append({"time": time, "file": f"{run_name}/{time}.png", "heightMax": round(top, 2)})

    manifest = {
        "model": model,
        "kind": "waves",
        "source": source,
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
    print(f"{model} run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB, highest {highest:.1f} m")


if __name__ == "__main__":
    main()
