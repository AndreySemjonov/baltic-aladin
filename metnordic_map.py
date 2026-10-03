"""Wind map frames for the Latvian coast from MET Nordic 1 km (MET Norway open data).

MET Norway's MET Nordic forecast has a new run every hour, 1 km grid, about 58 hours
ahead. This cuts our box out on their server (OPeNDAP), reprojects it to Web
Mercator (the projection of Apple's and other web maps, so an image lines up with
the map by its corner coordinates) at about 1 km per pixel, and writes one PNG per
hour plus a manifest:

    <out>/map.json
    <out>/<run>/<unix time>.png   R = wind speed m/s x 5, G = direction x 256/360,
                                  B = gust m/s x 5 (0.2 m/s steps, up to 51 m/s)
    <out>/land.png                land fraction x 255 (0 = sea), same pixels

    python metnordic_map.py site/map
"""
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

SOURCE = "https://thredds.met.no/thredds/dodsC/metpplatest/met_forecast_1_0km_nordic_latest.nc"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"
# Lithuania, Latvia and Estonia with their coasts and the Gulf of Finland (until 3.10.2026 the
# Latvian coast, Gulf of Riga and western Estonia: 55.6-59.1°N, 20.4-25.4°E at 1 km).
SOUTH, NORTH, WEST, EAST = 53.8, 60.0, 20.0, 28.4
# MET Nordic's grid: +proj=lcc +lat_0=63 +lon_0=15 +lat_1=63 +lat_2=63 +R=6371000
R = 6371000.0
LAT0 = LAT1 = math.radians(63.0)
LON0 = math.radians(15.0)
X0, Y0, STEP = -897442.2, -1104322.0, 1000.0
# 1.25 km pixels keep a frame near 100 KB over the larger area.
KM_PER_PIXEL = 1.25
EARTH_WEB = 6378137.0  # Web Mercator sphere


def get(url, timeout=600):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply.read()


def lcc(lat, lon):
    """Degrees (arrays) -> MET Nordic grid metres."""
    n = math.sin(LAT1)
    f = math.cos(LAT1) * math.tan(math.pi / 4 + LAT1 / 2) ** n / n
    rho0 = R * f / math.tan(math.pi / 4 + LAT0 / 2) ** n
    rho = R * f / np.tan(np.pi / 4 + np.radians(lat) / 2) ** n
    theta = n * (np.radians(lon) - LON0)
    return rho * np.sin(theta), rho0 - rho * np.cos(theta)


def mercator_y(lat):
    return EARTH_WEB * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def output_grid():
    """Latitude/longitude of each output pixel centre (row 0 = north)."""
    scale = 1 / math.cos(math.radians((SOUTH + NORTH) / 2))
    pixel = KM_PER_PIXEL * 1000 * scale              # Web Mercator metres per pixel
    x_west, x_east = EARTH_WEB * math.radians(WEST), EARTH_WEB * math.radians(EAST)
    y_south, y_north = float(mercator_y(SOUTH)), float(mercator_y(NORTH))
    width, height = round((x_east - x_west) / pixel), round((y_north - y_south) / pixel)
    xs = x_west + (np.arange(width) + 0.5) * (x_east - x_west) / width
    ys = y_north - (np.arange(height) + 0.5) * (y_north - y_south) / height
    lon = np.degrees(xs / EARTH_WEB)
    lat = np.degrees(2 * np.arctan(np.exp(ys / EARTH_WEB)) - np.pi / 2)
    return np.meshgrid(lat, lon, indexing="ij"), width, height


def ascii_values(data, name):
    """Values of `name` in an OPeNDAP ASCII reply: "time[59]" followed by a line of
    numbers, or "name, value" for a single value."""
    lines = [line.strip() for line in data.decode().splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if line.startswith(name + "["):
            return [float(v) for v in lines[i + 1].split(",")]
        if line.startswith(name + ","):
            return [float(line.split(",")[1])]
    raise ValueError(f"{name} not in the reply")


def dods_array(data, shape):
    """The first array of an OPeNDAP binary reply (big-endian float32)."""
    body = data[data.index(b"Data:\n") + 6:]
    count = int.from_bytes(body[:4], "big")
    values = np.frombuffer(body[8:8 + 4 * count], dtype=">f4").astype(np.float32)
    return values.reshape(shape)


def grid_index(lat, lon):
    """Nearest MET Nordic column and row of each output pixel, and their range."""
    gx, gy = lcc(lat, lon)
    col = np.rint((gx - X0) / STEP).astype(int)
    row = np.rint((gy - Y0) / STEP).astype(int)
    return col, row, (col.min(), col.max(), row.min(), row.max())


def land_fraction(lat, lon):
    """MET Nordic's land fraction (0 = sea) at each pixel; other maps of this coast use it too."""
    col, row, (c0, c1, r0, r1) = grid_index(lat, lon)
    query = f"land_area_fraction[{r0}:1:{r1}][{c0}:1:{c1}]"
    land = dods_array(get(f"{SOURCE}.dods?" + urllib.request.quote(query, safe=":,")), (r1 - r0 + 1, c1 - c0 + 1))
    return np.clip(np.nan_to_num(land[row - r0, col - c0], nan=1.0), 0, 1)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/map")
    (lat, lon), width, height = output_grid()
    col, row, (c0, c1, r0, r1) = grid_index(lat, lon)

    times = ascii_values(get(f"{SOURCE}.ascii?time"), "time")
    run = ascii_values(get(f"{SOURCE}.ascii?forecast_reference_time"), "forecast_reference_time")[0]
    steps = [i for i, t in enumerate(times) if t >= run]
    first, last = steps[0], steps[-1]
    fields = {}
    for key, name in (("speed", "wind_speed_10m"), ("direction", "wind_direction_10m"), ("gust", "wind_speed_of_gust")):
        query = f"{name}[{first}:1:{last}][{r0}:1:{r1}][{c0}:1:{c1}]"
        raw = get(f"{SOURCE}.dods?" + urllib.request.quote(query, safe=":,"))
        fields[key] = dods_array(raw, (last - first + 1, r1 - r0 + 1, c1 - c0 + 1))

    # Land fraction 0-1 (0 = sea), once per map: the app shows the wind strongly over
    # the sea and faintly over land, so the coast stays visible.
    land = land_fraction(lat, lon)
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.rint(land * 255).astype(np.uint8), "L").save(out / "land.png", optimize=True)

    run_name = datetime.fromtimestamp(run, timezone.utc).strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for k, step in enumerate(range(first, last + 1)):
        speed = np.nan_to_num(fields["speed"][k][row - r0, col - c0])
        direction = np.nan_to_num(fields["direction"][k][row - r0, col - c0])
        gust = np.nan_to_num(fields["gust"][k][row - r0, col - c0])
        rgb = np.stack([np.clip(np.rint(speed * 5), 0, 255),
                        np.mod(np.rint(direction * 256 / 360), 256),
                        np.clip(np.rint(gust * 5), 0, 255)], axis=-1).astype(np.uint8)
        name = f"{int(times[step])}.png"
        Image.fromarray(rgb, "RGB").save(folder / name, optimize=True)
        frames.append({"time": int(times[step]), "file": f"{run_name}/{name}"})
    manifest = {
        "model": "MET Nordic 1 km",
        "source": "MET Norway, MET Nordic forecast, CC BY 4.0: https://thredds.met.no/",
        "run": datetime.fromtimestamp(run, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "projection": "Web Mercator (EPSG:3857); the image spans the bounds edge to edge",
        "bounds": {"south": SOUTH, "north": NORTH, "west": WEST, "east": EAST},
        "width": width, "height": height,
        "encoding": {"red": "wind speed m/s x 5", "green": "direction (from) degrees x 256/360",
                     "blue": "gust m/s x 5"},
        "land": "land.png",
        "frames": frames,
    }
    (out / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"MET Nordic run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
