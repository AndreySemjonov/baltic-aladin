"""Rain map frames over Lithuania, Latvia and Estonia from MET Nordic 1 km (MET Norway open
data, CC BY 4.0): the precipitation and cloud cover of each forecast hour (about 57 hours, a new
run every hour), on the water map's Web Mercator box at 2.5 km pixels. The app shows the radar
(EUMETNET OPERA) and the satellite's clouds (Meteosat), read by the app itself, before now and
these frames after it.

    <out>/map.json                  "kind": "rain"
    <out>/<run>/<unix time>.png     RGB, read as raw numbers:
        red = sqrt(precipitation mm in the hour) x 50 (0 = none; 255 = 26 mm)
        green = cloud cover x 255 (0 = clear, 255 = overcast); "clouds": true in map.json

    python rain_map.py site/rain
    python rain_map.py --check                   print the newest run (MET Nordic's)
"""
import http.client
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import maplib
import metnordic_map as nordic

SOUTH, NORTH, WEST, EAST = nordic.SOUTH, nordic.NORTH, nordic.WEST, nordic.EAST
KM_PER_PIXEL = 2.5
# Every second MET Nordic row and column (2 km): enough for 2.5 km pixels, a quarter of the bytes.
STRIDE = 2


def main():
    if sys.argv[1:] == ["--check"]:
        print(nordic.newest_run())
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/rain")
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, KM_PER_PIXEL)
    col, row, (c0, c1, r0, r1) = nordic.grid_index(lat, lon)
    c0, r0 = c0 - c0 % STRIDE, r0 - r0 % STRIDE

    times = nordic.ascii_values(nordic.get(f"{nordic.SOURCE}.ascii?time"), "time")
    run = nordic.ascii_values(nordic.get(f"{nordic.SOURCE}.ascii?forecast_reference_time"), "forecast_reference_time")[0]
    steps = [i for i, t in enumerate(times) if t >= run]
    first, last = steps[0], steps[-1]
    rows = (r1 - r0) // STRIDE + 1
    cols = (c1 - c0) // STRIDE + 1

    def strided(name):
        """Every second row and column of a variable over the box, all hours; up to three tries
        (MET Norway's server sometimes drops a long reply)."""
        query = f"{name}[{first}:1:{last}][{r0}:{STRIDE}:{r1}][{c0}:{STRIDE}:{c1}]"
        for attempt in range(3):
            try:
                raw = nordic.get(f"{nordic.SOURCE}.dods?" + urllib.request.quote(query, safe=":,"))
                return nordic.dods_array(raw, (last - first + 1, rows, cols))
            except (OSError, ValueError, http.client.IncompleteRead):
                if attempt == 2:
                    raise

    rain = strided("precipitation_amount")
    cloud = strided("cloud_area_fraction")
    sample_row = np.clip(np.rint((row - r0) / STRIDE).astype(int), 0, rows - 1)
    sample_col = np.clip(np.rint((col - c0) / STRIDE).astype(int), 0, cols - 1)

    run_name = datetime.fromtimestamp(run, timezone.utc).strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for k, step in enumerate(range(first, last + 1)):
        mm = np.clip(np.nan_to_num(rain[k][sample_row, sample_col]), 0, None)
        red = np.clip(np.rint(np.sqrt(mm) * 50), 0, 255).astype(np.uint8)
        cover = np.clip(np.nan_to_num(cloud[k][sample_row, sample_col]), 0, 1)
        green = np.rint(cover * 255).astype(np.uint8)
        rgb = np.stack([red, green, np.zeros_like(red)], axis=-1)
        name = f"{int(times[step])}.png"
        Image.fromarray(rgb, "RGB").save(folder / name, optimize=True)
        frames.append({"time": int(times[step]), "file": f"{run_name}/{name}"})
    manifest = {
        "model": "MET Nordic 1 km",
        "kind": "rain",
        "source": "MET Norway, MET Nordic forecast, CC BY 4.0: https://thredds.met.no/",
        "run": datetime.fromtimestamp(run, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "projection": "Web Mercator (EPSG:3857); the image spans the bounds edge to edge",
        "bounds": {"south": SOUTH, "north": NORTH, "west": WEST, "east": EAST},
        "width": width, "height": height,
        "encoding": {"red": "sqrt(precipitation mm in the hour) x 50 (0 = none)",
                     "green": "cloud cover x 255 (0 = clear, 255 = overcast)"},
        "clouds": True,
        "frames": frames,
    }
    (out / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"Rain (MET Nordic) run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
