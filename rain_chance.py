"""The chance of rain from MEPS, MET Norway's ensemble (open data, CC BY 4.0): the newest runs
lagged over 6 hours, 30 members at 2.5 km, about 61 hours ahead. For each hour, the share of the
members that bring at least 0.5 mm in that hour (rain you notice on the water), on the rain
map's grid. Members are read at every second grid point (5 km; a chance is smooth anyway), about
210 MB per update.

    <out>/map.json                  "kind": "rain-chance"
    <out>/<run>/<unix time>.png     red = chance x 255 (0 = no member brings rain), for the hour
                                    ending at that time

    python rain_chance.py site/rain-chance
    python rain_chance.py --check            print the newest run
"""
import http.client
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import maplib
import metnordic_map as nordic
import rain_map

SOURCE = "https://thredds.met.no/thredds/dodsC/mepslatest/meps_lagged_6_h_latest_2_5km_latest.nc"
VARIABLE = "precipitation_amount_acc"  # kg/m2 = mm, summed from the start of the file's first hour
THRESHOLD = 0.5                        # mm in the hour
MEMBERS = 30
# MEPS's grid: +proj=lcc +lat_0=63.3 +lon_0=15 +lat_1=63.3 +lat_2=63.3 +R=6371000, 2.5 km
R = 6371000.0
LAT0 = math.radians(63.3)
LON0 = math.radians(15.0)
X0, Y0, STEP = -1060084.0, -1332517.9, 2500.0
COLUMNS, ROWS = 949, 1069
STRIDE = 2
# Hours per request (about 3.4 MB each): well under the server's limit of about 140 MB a reply.
CHUNK_HOURS = 8


def grid_position(lat, lon):
    """Degrees (arrays) -> fractional MEPS column and row."""
    n = math.sin(LAT0)
    f = math.cos(LAT0) * math.tan(math.pi / 4 + LAT0 / 2) ** n / n
    rho0 = R * f / math.tan(math.pi / 4 + LAT0 / 2) ** n
    rho = R * f / np.tan(np.pi / 4 + np.radians(lat) / 2) ** n
    theta = n * (np.radians(lon) - LON0)
    x, y = rho * np.sin(theta), rho0 - rho * np.cos(theta)
    return (x - X0) / STEP, (y - Y0) / STEP


def newest_run():
    """The newest run, as YYYYMMDDHH (one small request)."""
    run = nordic.ascii_values(nordic.get(f"{SOURCE}.ascii?forecast_reference_time", timeout=60), "forecast_reference_time")[0]
    return datetime.fromtimestamp(run, timezone.utc).strftime("%Y%m%d%H")


def main():
    if sys.argv[1:] == ["--check"]:
        print(newest_run())
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/rain-chance")
    (lat, lon), width, height = maplib.output_grid(rain_map.SOUTH, rain_map.NORTH, rain_map.WEST, rain_map.EAST,
                                                   rain_map.KM_PER_PIXEL)
    fi, fj = grid_position(lat, lon)
    c0 = max(int(np.floor(fi.min())) - 1, 0)
    c1 = min(int(np.ceil(fi.max())) + 1, COLUMNS - 1)
    r0 = max(int(np.floor(fj.min())) - 1, 0)
    r1 = min(int(np.ceil(fj.max())) + 1, ROWS - 1)
    c0, r0 = c0 - c0 % STRIDE, r0 - r0 % STRIDE
    cols, rows = (c1 - c0) // STRIDE + 1, (r1 - r0) // STRIDE + 1
    # The output pixels on the strided grid; MEPS covers the whole box.
    sample = maplib.Bilinear((fi - c0) / STRIDE, (fj - r0) / STRIDE, cols, rows)

    times = nordic.ascii_values(nordic.get(f"{SOURCE}.ascii?time"), "time")
    run = nordic.ascii_values(nordic.get(f"{SOURCE}.ascii?forecast_reference_time"), "forecast_reference_time")[0]

    def hours(first, last):
        """All members' sums for hours first...last over the box; up to three tries (MET Norway's
        server sometimes drops a long reply)."""
        query = f"{VARIABLE}[{first}:1:{last}][0][0:1:{MEMBERS - 1}][{r0}:{STRIDE}:{r1}][{c0}:{STRIDE}:{c1}]"
        for attempt in range(3):
            try:
                raw = nordic.get(f"{SOURCE}.dods?" + urllib.request.quote(query, safe=":,"))
                values = nordic.dods_array(raw, (last - first + 1, MEMBERS, rows, cols))
                # Missing values (a member that doesn't reach so far): NaN.
                return np.where(values > 1e30, np.nan, values)
            except (OSError, ValueError, http.client.IncompleteRead):
                if attempt == 2:
                    raise

    run_name = datetime.fromtimestamp(run, timezone.utc).strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    previous = None
    for start in range(0, len(times), CHUNK_HOURS):
        end = min(start + CHUNK_HOURS - 1, len(times) - 1)
        chunk = hours(start, end)
        for k in range(chunk.shape[0]):
            summed = chunk[k]
            if previous is not None:
                hour = summed - previous
                known = np.isfinite(hour)
                wet = (np.where(known, hour, 0) >= THRESHOLD).sum(axis=0)
                count = known.sum(axis=0)
                chance = np.where(count > 0, wet / np.maximum(count, 1), 0).astype(np.float32)
                red = np.rint(np.clip(sample(chance), 0, 1) * 255).astype(np.uint8)
                time = int(times[start + k])
                rgb = np.stack([red, np.zeros_like(red), np.zeros_like(red)], axis=-1)
                Image.fromarray(rgb, "RGB").save(folder / f"{time}.png", optimize=True)
                frames.append({"time": time, "file": f"{run_name}/{time}.png"})
            previous = summed
    manifest = {
        "model": "MEPS ensemble",
        "kind": "rain-chance",
        "source": "MET Norway, MEPS ensemble (lagged 6 hours, 30 members), CC BY 4.0: https://thredds.met.no/",
        "run": datetime.fromtimestamp(run, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "projection": "Web Mercator (EPSG:3857); the image spans the bounds edge to edge",
        "bounds": {"south": rain_map.SOUTH, "north": rain_map.NORTH, "west": rain_map.WEST, "east": rain_map.EAST},
        "width": width, "height": height,
        "encoding": {"red": f"chance x 255: the share of members with {THRESHOLD} mm or more in the hour ending at the frame's time"},
        "threshold": THRESHOLD,
        "members": MEMBERS,
        "frames": frames,
    }
    (out / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"Rain chance (MEPS) run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
