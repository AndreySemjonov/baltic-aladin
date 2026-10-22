"""The UK Met Office's UKV (2 km over the British Isles and the North Sea, to 54 hours; the owner,
18.10.2026: the last European model the spot pages read from Open-Meteo): from Open-Meteo's open
data on AWS (CC BY 4.0, not its API; Met Office data), the runs every 3 hours (00, 06, 12 and 18 UTC until 21.10.2026), made into
our wind tiles (tiles/ukmo, zooms 4-6 as the other 2 km models) and the spots' values from its own
cells (spot_cells.py: points/ukmo.json, points/ukmo-wx.json).

Its grid: Lambert azimuthal equal-area round 54.9 N 2.5 W on a sphere of 6 371 229 m, 1042 x 970
points 2 km apart from 44.508755 N 17.152863 W (the south-west corner, rows south to north).

    python ukmo_tiles.py [--force] [--local DIR] [--hours N]
"""
import math
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

import quality
import spot_cells
import temp_map
import tiler
import omremote
import world_tiles

BUCKET = "https://openmeteo.s3.amazonaws.com/"
MODEL = "ukmo_uk_deterministic_2km"
NX, NY, X0, Y0, STEP = 1042, 970, -1158000.0, -1036000.0, 2000.0
RADIUS, LAT0, LON0 = 6371229.0, math.radians(54.9), math.radians(-2.5)
ZOOMS = range(4, 7)
FADE_KM = 40
RUN_HOURS = (0, 6, 12, 18)
CREDIT = "UKV, Met Office (via Open-Meteo's open data, CC BY 4.0)"


def place(lon, lat):
    """Column, row and weight (1 inside, falling to 0 over the last FADE_KM to the edge)."""
    p, l = np.radians(lat), np.radians(lon)
    k = np.sqrt(2 / (1 + np.sin(LAT0) * np.sin(p) + np.cos(LAT0) * np.cos(p) * np.cos(l - LON0)))
    x = RADIUS * k * np.cos(p) * np.sin(l - LON0)
    y = RADIUS * k * (np.cos(LAT0) * np.sin(p) - np.sin(LAT0) * np.cos(p) * np.cos(l - LON0))
    col, row = (x - X0) / STEP, (y - Y0) / STEP
    inside = (col >= 0) & (row >= 0) & (col <= NX - 1) & (row <= NY - 1)
    cells = np.minimum(np.minimum(col, NX - 1 - col), np.minimum(row, NY - 1 - row))
    return col, row, np.where(inside, np.clip(cells * STEP / 1000 / FADE_KM, 0, 1), 0)


def sample(values, col, row):
    c = np.clip(col, 0, NX - 1.001)
    r = np.clip(row, 0, NY - 1.001)
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
    fc, fr = c - c0, r - r0
    top = values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc
    bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc
    return top * (1 - fr) + bottom * fr


def newest_run():
    """The newest run with all its 55 hours there: every 3 hours (the owner, 21.10.2026: we took the
    00/06/12/18 UTC ones only; the UKV runs to 54 hours every 3 hours, 12 hours in between)."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 3 * 3, minute=0, second=0, microsecond=0)
    for back in range(6):
        run = start - timedelta(hours=3 * back)
        files = [k for k in world_tiles.s3_list(f"data_spatial/{MODEL}/{run.strftime('%Y/%m/%d/%H%MZ/')}") if k.endswith(".om")]
        if len(files) >= 55:
            return run, sorted(files)
    raise SystemExit("No complete UKV run found")


def main():
    import omfiles
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    run, keys = newest_run()
    out = tiler.Run("ukmo", run, ZOOMS, "UKV 2 km", CREDIT, "--force" in args, local)
    if not out.wanted:
        print(f"UKV {run:%Y%m%dT%HZ} already published")
        return
    if limit:
        keys = keys[:limit]
    started = time.time()
    placement = tiler.Placement(tiler.tiles_in_box((44.0, 62.5, -18.0, 16.0), ZOOMS), place)
    try:
        cells = spot_cells.SpotCells("ukmo", MODEL, weather="ukmo-wx")
    except Exception as error:
        print(f"UKV: the spots' cells left out ({error})", flush=True)
        cells = None
    print(f"UKV {run:%Y%m%dT%HZ}: {len(placement.parts)} tiles, {len(keys)} hours", flush=True)
    for key in keys:
        valid = datetime.strptime(key.rsplit("/", 1)[-1][:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
        t = int(valid.timestamp())
        handle = None
        # At home only the variables used, read in part (omremote.py; the owner, 21.10.2026).
        if omremote.PARTIAL:
            reader = omremote.Prefetched(BUCKET + key, ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "temperature_2m", "precipitation", "cloud_cover", "relative_humidity_2m", "pressure_msl"])
        else:
            handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
            handle.close()
        for attempt in range(4 if handle is not None else 0):
            try:
                request = urllib.request.Request(BUCKET + key, headers={"User-Agent": "MeteoGust helper"})
                with urllib.request.urlopen(request, timeout=600) as reply, open(handle.name, "wb") as file:
                    while True:
                        block = reply.read(1 << 22)
                        if not block:
                            break
                        file.write(block)
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
        if handle is not None:
            reader = omfiles.OmFileReader.from_path(handle.name)

        def field(name):
            try:
                return reader.get_child_by_name(name).read_array((slice(None), slice(None))).astype(np.float32)
            except Exception:
                return None
        speed, direction, gust = field("wind_speed_10m"), field("wind_direction_10m"), field("wind_gusts_10m")
        air, rain, cloud = field("temperature_2m"), field("precipitation"), field("cloud_cover")
        humidity, msl = field("relative_humidity_2m"), field("pressure_msl")
        reader.close()
        if handle is not None:
            os.unlink(handle.name)
        if speed is None or direction is None:
            print(f"  {valid:%d %H:%M}: no wind", flush=True)
            continue
        radians = np.radians(np.nan_to_num(direction))
        east, north = -np.nan_to_num(speed) * np.sin(radians), -np.nan_to_num(speed) * np.cos(radians)
        gust = np.maximum(np.nan_to_num(gust), np.nan_to_num(speed)) if gust is not None else np.nan_to_num(speed)
        if cells is not None:
            cells.add_placed(t, place, sample, east, north, gust, air=air, rain=rain, cloud=cloud)
        if not out.checked("UKV", t, quality.wind(east, north, gust)):
            continue
        out.put(t, tiler.pack(placement, sample, lambda at: tiler.wind_rgb(at(east), at(north), at(gust)),
                              valid=lambda at: np.isfinite(at(speed))))
        # The air and the rain as weather layers too (<time>-temp.pack, <time>-rain.pack: the weather
        # mix's; the owner, 18.10.2026), as the world's: air, feels like, sea-level pressure; the
        # hour's rain and the cloud cover.
        if air is not None:
            rh = np.clip(np.nan_to_num(humidity, nan=60) / 100, 0, 1) if humidity is not None else np.full(air.shape, 0.6, np.float32)
            feels = temp_map.feels_like(air, np.nan_to_num(speed), rh)
            hpa = msl if msl is not None else np.full(air.shape, np.nan, np.float32)
            out.put(t, tiler.pack(placement, sample, lambda at: tiler.temp_rgb(at(air), at(feels), at(hpa)),
                                  valid=lambda at: np.isfinite(at(air))), "temp")
        if rain is not None:
            cover = np.clip(np.nan_to_num(cloud) / 100, 0, 1) if cloud is not None else np.zeros_like(rain)
            wet = np.maximum(np.nan_to_num(rain), 0)
            out.put(t, tiler.pack(placement, sample, lambda at: tiler.rain_rgb(at(wet), at(cover)),
                                  valid=lambda at: np.isfinite(at(rain))), "rain")
        print(f"  {valid:%d %H:%M}: {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    out.finish()
    if cells is not None:
        cells.publish(out.folder, local and local + "/points")
    print(f"UKV done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up, as bucket_tiles.py: omfiles crashed Python's exit after the work was done
    # ("double free or corruption", every home run from 21.10.2026: the job marked failed; the owner, 22.10.2026).
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
