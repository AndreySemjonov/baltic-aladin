"""Air temperature map frames from MET Nordic 1 km (MET Norway open data, CC BY 4.0): the air
temperature at 2 m and how it feels with the wind, each forecast hour (about 58 hours, a new run
every hour), on the rain map's Web Mercator box at 2.5 km pixels.

Feels like: the wind chill (Environment Canada's formula, 10 m wind) up to 10 °C; the Australian
Bureau of Meteorology's apparent temperature (wind and humidity, no sun) from 20 °C; a blend of
the two between, so there is no jump.

    <out>/map.json                  "kind": "temperature"
    <out>/<run>/<unix time>.png     RGB, read as raw numbers:
        red   = (air °C + 40) x 3 + 1 (1-255: -40 to +44.7 °C); 0 = no data
        green = (feels like °C + 40) x 3 + 1, the same way
        blue  = (sea-level pressure hPa - 940) x 2 (1-255: 940.5 to 1067.5 hPa); 0 = no data,
                for the app's isobars

    python temp_map.py site/temp
    python temp_map.py --check                   print the newest run (MET Nordic's)
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


def encode(celsius):
    """°C as the frames' byte: (°C + 40) x 3 + 1, 0 where unknown."""
    value = np.clip(np.rint((celsius + 40) * 3) + 1, 1, 255)
    return np.where(np.isfinite(celsius), value, 0).astype(np.uint8)


def feels_like(celsius, wind, humidity):
    """How it feels (°C) from the air (°C), the 10 m wind (m/s) and the relative humidity (0-1)."""
    kmh = np.maximum(wind * 3.6, 0)
    v16 = np.power(np.maximum(kmh, 4.8), 0.16)
    chill = 13.12 + 0.6215 * celsius - 11.37 * v16 + 0.3965 * celsius * v16
    chill = np.where(kmh > 4.8, np.minimum(chill, celsius), celsius)
    # Water vapour pressure (hPa) for the apparent temperature.
    vapour = np.clip(humidity, 0, 1) * 6.105 * np.exp(17.27 * celsius / (237.7 + celsius))
    apparent = celsius + 0.33 * vapour - 0.70 * wind - 4.0
    share = np.clip((celsius - 10) / 10, 0, 1)
    return chill * (1 - share) + apparent * share


def main():
    if sys.argv[1:] == ["--check"]:
        print(nordic.newest_run())
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/temp")
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

    kelvin = strided("air_temperature_2m")
    wind = strided("wind_speed_10m")
    humidity = strided("relative_humidity_2m")
    pressure = strided("air_pressure_at_sea_level")
    sample_row = np.clip(np.rint((row - r0) / STRIDE).astype(int), 0, rows - 1)
    sample_col = np.clip(np.rint((col - c0) / STRIDE).astype(int), 0, cols - 1)

    run_name = datetime.fromtimestamp(run, timezone.utc).strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    low, high = np.inf, -np.inf
    for k, step in enumerate(range(first, last + 1)):
        t = kelvin[k][sample_row, sample_col].astype(np.float32)
        # Missing values come as huge numbers or NaN.
        t = np.where((t > 150) & (t < 350), t - 273.15, np.nan)
        w = np.nan_to_num(wind[k][sample_row, sample_col].astype(np.float32))
        w = np.where(np.abs(w) < 100, w, 0)
        h = humidity[k][sample_row, sample_col].astype(np.float32)
        h = np.where((h >= 0) & (h <= 1.5), h, 0.7)
        feel = feels_like(t, w, h)
        if np.isfinite(t).any():
            low, high = min(low, float(np.nanmin(t))), max(high, float(np.nanmax(t)))
        hpa = pressure[k][sample_row, sample_col].astype(np.float32) / 100
        hpa = np.where((hpa > 850) & (hpa < 1100), hpa, np.nan)
        blue = np.where(np.isfinite(hpa), np.clip(np.rint((hpa - 940) * 2), 1, 255), 0).astype(np.uint8)
        rgb = np.stack([encode(t), encode(feel), blue], axis=-1)
        name = f"{int(times[step])}.png"
        Image.fromarray(rgb, "RGB").save(folder / name, optimize=True)
        frames.append({"time": int(times[step]), "file": f"{run_name}/{name}"})
    manifest = {
        "model": "MET Nordic 1 km",
        "kind": "temperature",
        "source": "MET Norway, MET Nordic forecast, CC BY 4.0: https://thredds.met.no/",
        "run": datetime.fromtimestamp(run, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "projection": "Web Mercator (EPSG:3857); the image spans the bounds edge to edge",
        "bounds": {"south": SOUTH, "north": NORTH, "west": WEST, "east": EAST},
        "width": width, "height": height,
        "encoding": {"red": "(air temperature degC + 40) x 3 + 1; 0 = no data",
                     "green": "(feels like degC + 40) x 3 + 1: wind chill to 10 degC, apparent temperature from 20, blended between",
                     "blue": "(sea-level pressure hPa - 940) x 2; 0 = no data"},
        "pressure": True,
        "temperatureRange": [round(low, 1), round(high, 1)] if np.isfinite(low) else None,
        "frames": frames,
    }
    (out / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"Air temperature (MET Nordic) run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
