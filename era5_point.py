"""Ten years of hourly wind at one place from ERA5 (the owner, 19.10.2026: the spots' best months off
Open-Meteo's archive API, for its licence): Copernicus ERA5 (0.25°) from Open-Meteo's open data on
AWS (CC BY 4.0, not its API), the nearest cell's 10 m wind, for the admin panel's "Compute from
history" to count the good days with any directions.

    climate/era5/<row>-<col>.json   {cell {lat, lon}, start (unix, the first hour), years, zone,
                                     speed [0.1 m/s an hour], direction [whole degrees, from]},
                                     null where ERA5 has none

    python era5_point.py LAT LON [--zone Europe/Riga] [--local DIR]
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BUCKET = "openmeteo/data/copernicus_era5/"
UPLOAD = "https://data.meteogust.com/tiles/upload/"
# The years' own files up to 2021, after that the 21-day chunks (hour since 1970 // 504).
YEAR_FILES_UNTIL = 2021
CHUNK_HOURS = 504
YEARS = 10


def cell(lat, lon):
    """ERA5's nearest cell: rows from 90° S, columns from 180° W, every 0.25°."""
    row = min(720, max(0, round((lat + 90) / 0.25)))
    col = round((lon + 180) / 0.25) % 1440
    return row, col


def read_point(fs, variable, row, col, first, last):
    """The variable's hours at the cell from Jan 1 of `first` to Dec 31 of `last`, as float32 (NaN none)."""
    import omfiles

    def year_file(year):
        reader = omfiles.OmFileReader.from_fsspec(fs, f"{BUCKET}{variable}/year_{year}.om")
        try:
            return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()) // 3600, np.asarray(reader[row, col, :], dtype=np.float32)
        finally:
            reader.close()

    def chunk_file(index):
        reader = omfiles.OmFileReader.from_fsspec(fs, f"{BUCKET}{variable}/chunk_{index}.om")
        try:
            return index * CHUNK_HOURS, np.asarray(reader[row, col, :], dtype=np.float32)
        finally:
            reader.close()

    start = int(datetime(first, 1, 1, tzinfo=timezone.utc).timestamp()) // 3600
    end = int(datetime(last + 1, 1, 1, tzinfo=timezone.utc).timestamp()) // 3600
    jobs = [(year_file, y) for y in range(first, min(last, YEAR_FILES_UNTIL) + 1)]
    after = max(start, int(datetime(YEAR_FILES_UNTIL + 1, 1, 1, tzinfo=timezone.utc).timestamp()) // 3600)
    if after < end:
        jobs += [(chunk_file, i) for i in range(after // CHUNK_HOURS, (end - 1) // CHUNK_HOURS + 1)]
    out = np.full(end - start, np.nan, dtype=np.float32)
    with ThreadPoolExecutor(8) as pool:
        for begin, values in pool.map(lambda job: job[0](job[1]), jobs):
            # Each file's hours where they fall in the years asked (the chunks run over the edges).
            lo, hi = max(begin, start), min(begin + len(values), end)
            if lo < hi:
                out[lo - start:hi - start] = values[lo - begin:hi - begin]
    return start * 3600, out


def upload(path, data):
    """PUT on our server (station-history /tiles/upload, the INGEST_TOKEN secret), with retries."""
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    for attempt in range(4):
        try:
            request = urllib.request.Request(UPLOAD + path, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "User-Agent": "MeteoGust helper"})
            with urllib.request.urlopen(request, timeout=120) as reply:
                return reply.read().decode()
        except (urllib.error.URLError, OSError) as error:
            if (isinstance(error, urllib.error.HTTPError) and error.code < 500) or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def make(lat, lon, zone=None, local=None):
    import s3fs
    fs = s3fs.S3FileSystem(anon=True, default_block_size=65536)
    last = datetime.now(timezone.utc).year - 1
    first = last - YEARS + 1
    row, col = cell(lat, lon)
    started = time.time()
    start, u = read_point(fs, "wind_u_component_10m", row, col, first, last)
    _, v = read_point(fs, "wind_v_component_10m", row, col, first, last)
    speed = np.hypot(u, v)
    # Where the wind comes from, in degrees (meteorological).
    direction = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
    ok = np.isfinite(speed)
    out = {
        "cell": {"lat": round(row * 0.25 - 90, 2), "lon": round(col * 0.25 - 180, 2)},
        "start": start, "years": f"{first}–{last}", "zone": zone, "source": "ERA5 (Copernicus), Open-Meteo open data",
        "speed": [int(round(s * 10)) if k else None for s, k in zip(speed.tolist(), ok.tolist())],
        "direction": [int(round(d)) % 360 if k else None for d, k in zip(direction.tolist(), ok.tolist())],
    }
    have = int(ok.sum())
    print(f"cell {row}-{col} ({out['cell']['lat']}, {out['cell']['lon']}): {have} of {len(speed)} hours, "
          f"mean {float(np.nanmean(speed)):.1f} m/s, {time.time() - started:.0f} s", flush=True)
    if have < len(speed) * 0.9:
        raise SystemExit("ERA5 short at this cell: not published")
    data = json.dumps(out, separators=(",", ":")).encode()
    name = f"climate/era5/{row}-{col}.json"
    if local:
        path = Path(local) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print("written", path, len(data), "bytes")
    else:
        print(name, upload(name, data).strip(), len(data), "bytes", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    zone = args[args.index("--zone") + 1] if "--zone" in args else None
    numbers = [a for a in args if not a.startswith("--") and a not in (local, zone)]
    lat, lon = float(numbers[0]), float(numbers[1])
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise SystemExit("LAT LON out of range")
    make(lat, lon, zone or None, local)
