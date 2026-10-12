"""FMI's HARMONIE 2.5 km (HARM-FI) at its whole area as tiles (docs/TILES_PLAN.md in the app's
repository, phase 2; the owner, 12.10.2026: every model at its full area). FMI's open data service
cuts its Scandinavian domain (about 49.8-75N, 10W-45E) to a box and turns it into latitude and
longitude on request; asked for all of it, one GRIB2 file of the wind's parts and gust for the
run's hours. Through the shared tiler into tiles/harm-fi at zooms 4-6, its weight fading over the
last 50 km to the area's edge. Each hour is made as soon as its three fields are read.

    python harmfi_tiles.py [--force] [--local DIR]
"""
import re
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import eccodes as ec
import numpy as np

import harmonie_map
import maplib
import tiler

ZOOMS = range(4, 7)
FADE_KM = 50
# FMI clips the box to its domain.
ASK = (-10.0, 49.0, 45.0, 76.0)
CREDIT = "FMI, HARMONIE forecast (CC BY 4.0)"


def newest_file():
    listing = harmonie_map.get(harmonie_map.WFS.format(west=ASK[0], south=ASK[1], east=ASK[2], north=ASK[3])).decode()
    links = [link.replace("&amp;", "&") for link in re.findall(r"<gml:fileReference>([^<]+)", listing)]
    if not links:
        raise SystemExit("FMI offers no HARMONIE run")
    runs = [(re.search(r"origintime=([0-9T:\-]+Z)", link).group(1), link) for link in links]
    origin, link = max(runs)
    return link, datetime.strptime(origin, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    link, run_time = newest_file()
    out = tiler.Run("harm-fi", run_time, ZOOMS, "HARM-FI 2.5 km", CREDIT, "--force" in args, local)
    if not out.wanted:
        print(f"HARM-FI {run_time:%Y%m%d%H} already published")
        return
    started = time.time()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder, "harmonie.grib2")
        request = urllib.request.Request(link, headers={"User-Agent": maplib.USER_AGENT})
        with urllib.request.urlopen(request, timeout=1800) as reply, open(path, "wb") as f:
            while chunk := reply.read(1 << 22):
                f.write(chunk)
        print(f"HARM-FI {run_time:%Y%m%d%H}: {path.stat().st_size / 1e6:.0f} MB in {time.time() - started:.0f} s", flush=True)
        placement = sample = None
        pending = {}
        with open(path, "rb") as f:
            while (gid := ec.codes_grib_new_from_file(f)) is not None:
                try:
                    if placement is None:
                        placement, sample = grid_tools({key: ec.codes_get(gid, key) for key in (
                            "Ni", "Nj", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                            "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees", "jScansPositively")})
                    date, hhmm = ec.codes_get(gid, "validityDate"), ec.codes_get(gid, "validityTime")
                    t = int(datetime.strptime(f"{date}{hhmm:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp())
                    values = ec.codes_get_values(gid).astype(np.float32)
                    # The box's corners off FMI's slanted domain: no data (as AROME's, 12.10.2026).
                    if ec.codes_get(gid, "bitmapPresent"):
                        values[values == ec.codes_get(gid, "missingValue")] = np.nan
                    values = values.reshape(ec.codes_get(gid, "Nj"), ec.codes_get(gid, "Ni"))
                    pending.setdefault(t, {})[ec.codes_get(gid, "shortName")] = values
                finally:
                    ec.codes_release(gid)
                step = pending.get(t, {})
                if {"10u", "10v", "10fg"} <= step.keys():
                    pending.pop(t)
                    if t >= run_time.timestamp():
                        u, v, g = step["10u"], step["10v"], step["10fg"]
                        out.put(t, tiler.pack(placement, sample, lambda at: tiler.wind_rgb(at(u), at(v), at(g)),
                                              valid=lambda at: np.isfinite(at(u)) & np.isfinite(at(v))))
    out.finish()
    print(f"HARM-FI {run_time:%Y%m%d%H}: done in {time.time() - started:.0f} s")


def grid_tools(grid):
    ni, nj = grid["Ni"], grid["Nj"]
    lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
    if lon0 > 180:
        lon0 -= 360
    dlon = grid["iDirectionIncrementInDegrees"]
    dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
    lat1 = lat0 + dlat * (nj - 1)
    box = (min(lat0, lat1), max(lat0, lat1), lon0, lon0 + dlon * (ni - 1))
    print(f"HARM-FI grid {ni} x {nj} over {tuple(round(x, 2) for x in box)}", flush=True)

    def place(lon, lat):
        col, row = (lon - lon0) / dlon, (lat - lat0) / dlat
        inside = (col >= 0) & (row >= 0) & (col <= ni - 1) & (row <= nj - 1)
        return col, row, np.where(inside, tiler.edge_weight(lon, lat, box, FADE_KM), 0)

    def sample(values, col, row):
        c = np.clip(col, 0, ni - 1.001)
        r = np.clip(row, 0, nj - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        top = values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc
        bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr

    return tiler.Placement(tiler.tiles_in_box(box, ZOOMS), place), sample


if __name__ == "__main__":
    main()
