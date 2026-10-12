"""World weather tiles from GFS (NOAA, public domain) and ECMWF open data (CC BY 4.0).

Plan step 1 for the world (the owner, 12.10.2026): each hour of a run becomes one pack of
weather tiles on the web map's grid (Web Mercator, 256-pixel tiles, zooms 0-3: the models'
0.25° grid is about 28 km, zoom 3 about 20 km a pixel at the equator), the tiles holding
values, not colours (red and green the wind's east and north parts m/s x 4 + 128, blue the
gust m/s x 5; the land once per run in land.pack), so the app colours them on the phone as the Baltic maps. A
pack is "MGT1", the header's length (4 bytes, little-endian), the header (JSON: each tile's
offset from the header's end and its length) and the tiles (PNG); the app reads the header and then only the tiles it shows.
The packs and tiles/<model>/index.json (the run's hours) go to the file store through our
service (station-history /tiles/upload, the INGEST_TOKEN secret), the index last.

    python world_tiles.py gfs|ecmwf              build and upload the newest run, if new
    python world_tiles.py gfs|ecmwf --force      even if it's the published one
    python world_tiles.py gfs|ecmwf --local DIR  write to DIR, no upload (a test; --steps 0,24)
"""
import io
import json
import math
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import ecmwf_map
import gfs_map
import maplib
import temp_map

ZOOMS = range(0, 4)
# Pixels a tile side at each zoom: 256, except zoom 3 at the models' own detail (1440 columns
# round the world, 0.25°): more pixels there only made the packs bigger (5 MB an hour).
SIZES = {0: 256, 1: 256, 2: 256, 3: 180}
UPLOAD = "https://data.meteogust.com/tiles/upload/"
PUBLISHED = "https://files.meteogust.com/tiles/{model}/index.json"

GFS_STEPS = list(range(0, 385, 3))
GFS_FILTER = ("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?dir=%2Fgfs.{day}%2F{hour}%2Fatmos"
              "&file=gfs.t{hour}z.pgrb2.0p25.f{step:03d}&var_UGRD=on&var_VGRD=on&var_GUST=on&var_LAND=on"
              "&lev_10_m_above_ground=on&lev_surface=on")

MODELS = {
    "gfs": {"name": "GFS 0.25°", "credit": "NOAA NCEP, GFS (public domain)", "gust": "surface gust"},
    "ecmwf": {"name": "ECMWF 0.25°", "credit": "ECMWF open data (CC BY 4.0)", "gust": "maximum since the previous step"},
}


# MARK: The models' fields

def gfs_steps(run, steps):
    """(step, {"10u", "10v", "gust", "lsm"}: values, grid) for each step, downloaded ahead."""
    def fetch(step):
        url = GFS_FILTER.format(day=run.strftime("%Y%m%d"), hour=run.strftime("%H"), step=step)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(gfs_map.request(url), timeout=180) as reply:
                    return step, reply.read()
            except (urllib.error.URLError, OSError):
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
    with ThreadPoolExecutor(max_workers=3) as pool:
        for step, data in pool.map(fetch, steps):
            fields, grid = gfs_map.decode(data)
            yield step, fields, grid


def ecmwf_steps(run, steps):
    """The same from ECMWF's open data: 10 m wind, the gust and (at step 0) the land-sea mask."""
    def fetch(step):
        # The wind, and for the world's temperature and rain maps the air (2 m, its dew point),
        # the sea-level pressure, the precipitation so far and the cloud cover.
        wanted = ("10u", "10v", "10fg", "2t", "2d", "msl", "tp", "tcc") + (("lsm",) if step == 0 else ())
        index = [json.loads(line) for line in ecmwf_map.get(ecmwf_map.file_url(run, step, "index")).decode().splitlines()
                 if line.strip()]
        messages = {}
        for entry in index:
            if entry.get("levtype") == "sfc" and entry.get("param") in wanted:
                start, length = entry["_offset"], entry["_length"]
                messages[entry["param"]] = ecmwf_map.get(ecmwf_map.file_url(run, step, "grib2"), start, start + length - 1)
        return step, messages
    with ThreadPoolExecutor(max_workers=4) as pool:
        for step, messages in pool.map(fetch, steps):
            fields, grid = ecmwf_map.decode(messages)
            if "10fg" in fields:
                fields["gust"] = fields.pop("10fg")
            yield step, fields, grid


# MARK: Tiles

def tile_coordinates(z, x, y):
    """Latitude and longitude of each pixel centre of tile z/x/y (row 0 north)."""
    n, size = 2 ** z, SIZES[z]
    xs = (x + (np.arange(size) + 0.5) / size) / n
    ys = (y + (np.arange(size) + 0.5) / size) / n
    lon = xs * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * ys))))
    return np.meshgrid(lat, lon, indexing="ij")


class Sampler:
    """Bilinear from a global regular grid to every tile's pixels, the grid wrapped round at
    the date line (its first column again after its last)."""

    def __init__(self, grid):
        self.columns, self.rows = grid["Ni"], grid["Nj"]
        lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
        dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
        dlon = grid["iDirectionIncrementInDegrees"]
        self.tiles = {}
        for z in ZOOMS:
            for x in range(2 ** z):
                for y in range(2 ** z):
                    lat, lon = tile_coordinates(z, x, y)
                    fi = np.mod(lon - lon0, 360) / dlon
                    fj = (lat - lat0) / dlat
                    self.tiles[(z, x, y)] = maplib.Bilinear(fi, fj, self.columns + 1, self.rows)

    def __call__(self, key, values):
        wrapped = np.concatenate([values, values[:, :1]], axis=1)
        return self.tiles[key](wrapped)


def encode(tiles, sizes=None):
    """Packed: "MGT1", the header's length, the header, the PNGs ({key: PNG bytes}); `sizes`:
    each zoom's tile side (the weather tiles' by default)."""
    sizes = sizes or SIZES
    header, offset, body = {}, 0, []
    for key, data in tiles.items():
        header["%d/%d/%d" % key] = [offset, len(data)]
        body.append(data)
        offset += len(data)
    # The tiles' offsets count from the end of the header (8 + its length).
    head = json.dumps({"sizes": {str(z): sizes[z] for z in sorted(sizes)}, "zooms": [min(sizes), max(sizes)], "tiles": header},
                      separators=(",", ":")).encode()
    return b"MGT1" + struct.pack("<I", len(head)) + head + b"".join(body)


def png(array, mode):
    buffer = io.BytesIO()
    Image.fromarray(array, mode).save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue()


def pack(sample, fields):
    """One hour: the wind's east and north parts (m/s x 4 + 128: smooth, so they pack small;
    where it comes from wraps round and didn't) and the gust (m/s x 5), as RGB tiles."""
    tiles = {}
    for key in sample.tiles:
        u, v = np.nan_to_num(sample(key, fields["10u"])), np.nan_to_num(sample(key, fields["10v"]))
        speed = np.hypot(u, v)
        gust = np.maximum(np.nan_to_num(sample(key, fields["gust"])) if "gust" in fields else speed, speed)
        rgb = np.stack([np.clip(np.rint(u * 4 + 128), 0, 255), np.clip(np.rint(v * 4 + 128), 0, 255),
                        np.clip(np.rint(gust * 5), 0, 255)], axis=-1).astype(np.uint8)
        tiles[key] = png(rgb, "RGB")
    return encode(tiles)


def temp_pack(sample, fields):
    """The air as the Baltic temperature map: red (air °C + 40) x 3 + 1, green how it feels the
    same way (temp_map.feels_like: the wind chill to 10 °C, the apparent temperature from 20),
    blue (sea-level pressure hPa - 940) x 2; 0 = unknown."""
    tiles = {}
    for key in sample.tiles:
        celsius = sample(key, fields["2t"]) - 273.15
        u, v = sample(key, fields["10u"]), sample(key, fields["10v"])
        if "2d" in fields:
            dew = sample(key, fields["2d"]) - 273.15
            humidity = np.clip(np.exp(17.625 * dew / (243.04 + dew)) / np.exp(17.625 * celsius / (243.04 + celsius)), 0, 1)
        else:
            humidity = np.full_like(celsius, 0.6)
        feels = temp_map.feels_like(celsius, np.hypot(u, v), humidity)
        hpa = sample(key, fields["msl"]) / 100 if "msl" in fields else np.full_like(celsius, np.nan)
        rgb = np.stack([temp_map.encode(celsius), temp_map.encode(feels),
                        np.where(np.isfinite(hpa), np.clip(np.rint((hpa - 940) * 2), 1, 255), 0)], axis=-1).astype(np.uint8)
        tiles[key] = png(rgb, "RGB")
    return encode(tiles)


def rain_pack(sample, fields, rate):
    """The rain as the Baltic rain map: red sqrt(mm an hour) x 50, green the cloud cover x 255."""
    tiles = {}
    for key in sample.tiles:
        mm = np.maximum(np.nan_to_num(sample(key, rate)), 0)
        cloud = np.nan_to_num(sample(key, fields["tcc"])) if "tcc" in fields else np.zeros_like(mm)
        if cloud.max() > 1.5:
            cloud = cloud / 100
        rgb = np.stack([np.clip(np.rint(np.sqrt(mm) * 50), 0, 255), np.clip(np.rint(cloud * 255), 0, 255),
                        np.zeros_like(mm)], axis=-1).astype(np.uint8)
        tiles[key] = png(rgb, "RGB")
    return encode(tiles)


def land_pack(sample, mask):
    """The run's land (0 sea - 255 land) as grey tiles, once (it doesn't change with the hours)."""
    return encode({key: png(np.clip(np.rint(np.nan_to_num(sample(key, mask)) * 255), 0, 255).astype(np.uint8), "L")
                   for key in sample.tiles})


# MARK: Publishing

def upload(path, data):
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    for attempt in range(4):
        try:
            request = urllib.request.Request(UPLOAD + path, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "User-Agent": maplib.USER_AGENT})
            with urllib.request.urlopen(request, timeout=300) as reply:
                return reply.read().decode()
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))
        except (urllib.error.URLError, OSError):
            if attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def published(model):
    try:
        with urllib.request.urlopen(PUBLISHED.format(model=model) + "?t=%d" % time.time(), timeout=30) as reply:
            return json.load(reply).get("runName")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main():
    args = sys.argv[1:]
    if not args or args[0] not in MODELS:
        raise SystemExit(__doc__)
    model = args[0]
    info = MODELS[model]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    run = gfs_map.newest_run() if model == "gfs" else ecmwf_map.newest_run()
    run_name = run.strftime("%Y%m%dT%HZ")
    if "--check" in args:
        print(run_name)
        return
    if local is None and "--force" not in args and published(model) == run_name:
        print(f"{model}: {run_name} is published already")
        return
    steps = GFS_STEPS if model == "gfs" else ecmwf_map.STEPS
    if "--steps" in args:
        steps = [int(s) for s in args[args.index("--steps") + 1].split(",")]
    source = gfs_steps(run, steps) if model == "gfs" else ecmwf_steps(run, steps)
    sample, land, frames, size, started = None, False, [], 0, time.time()
    # The step and precipitation before (ECMWF's is the total since the run's start).
    previous = [0, None]

    def put(name, data):
        if local:
            (local / name).parent.mkdir(parents=True, exist_ok=True)
            (local / name).write_bytes(data)
        else:
            upload(f"{model}/{name}", data)

    for step, fields, grid in source:
        if "10u" not in fields or "10v" not in fields:
            print(f"{model} step {step}: no wind, skipped")
            continue
        if sample is None:
            sample = Sampler(grid)
        if not land and "lsm" in fields:
            put(f"{run_name}/land.pack", land_pack(sample, fields["lsm"]))
            land = True
        data = pack(sample, fields)
        t = int((run + timedelta(hours=step)).timestamp())
        name = f"{run_name}/{t}.pack"
        put(name, data)
        frame = {"time": t, "file": name}
        size += len(data)
        # ECMWF: the world's temperature and rain too (the precipitation since the run's start,
        # so each step's rain is its difference from the step before, per hour).
        # (Optional: one that can't be uploaded is left out, the wind still goes.)
        if "2t" in fields:
            extra = temp_pack(sample, fields)
            try:
                put(f"{run_name}/{t}-temp.pack", extra)
                frame["temp"] = f"{run_name}/{t}-temp.pack"
                size += len(extra)
            except (urllib.error.URLError, OSError) as error:
                print(f"{model} step {step}: temperature not uploaded ({error})")
        if "tp" in fields:
            hours = step - previous[0] if previous[1] is not None else 0
            rate = (fields["tp"] - previous[1]) * 1000 / hours if hours > 0 else np.zeros_like(fields["tp"])
            previous[0], previous[1] = step, fields["tp"]
            extra = rain_pack(sample, fields, rate)
            try:
                put(f"{run_name}/{t}-rain.pack", extra)
                frame["rain"] = f"{run_name}/{t}-rain.pack"
                size += len(extra)
            except (urllib.error.URLError, OSError) as error:
                print(f"{model} step {step}: rain not uploaded ({error})")
        frames.append(frame)
    if not frames:
        raise SystemExit(f"{model}: no frames")
    index = {
        "model": info["name"], "credit": info["credit"], "run": run.strftime("%Y-%m-%dT%H:%MZ"), "runName": run_name,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "tileSizes": {str(z): SIZES[z] for z in ZOOMS},
        "zooms": [ZOOMS.start, ZOOMS.stop - 1], "projection": "Web Mercator tiles (z/x/y, y from the north)",
        "encoding": {"red": "wind towards the east m/s x 4 + 128", "green": "wind towards the north m/s x 4 + 128",
                     "blue": "gust m/s x 5 (" + info["gust"] + ")"},
        "land": f"{run_name}/land.pack" if land else None,
        "frames": frames,
    }
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (local / "index.json").write_bytes(body)
        print(f"{model} {run_name}: {len(frames)} packs, {size / 1e6:.0f} MB, {time.time() - started:.0f} s (local)")
    else:
        print(upload(f"{model}/index.json", body).strip())
        print(f"{model} {run_name}: {len(frames)} packs, {size / 1e6:.0f} MB, {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
