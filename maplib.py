"""Shared pieces of the wind map scripts: the Web Mercator output grid, interpolation
from regular latitude/longitude grids, the PNG frame encoding and the manifest.

Frames: one PNG per hour, red = wind speed m/s x 5, green = direction (from) x 256/360,
blue = gust m/s x 5. A frame with an alpha channel marks pixels outside the model's
area with alpha 0.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

EARTH_WEB = 6378137.0  # Web Mercator sphere
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"


def mercator_y(lat):
    return EARTH_WEB * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def output_grid(south, north, west, east, km_per_pixel):
    """Latitude/longitude of each output pixel centre (row 0 = north), about
    `km_per_pixel` across in the middle of the box."""
    scale = 1 / math.cos(math.radians((south + north) / 2))
    pixel = km_per_pixel * 1000 * scale
    x_west, x_east = EARTH_WEB * math.radians(west), EARTH_WEB * math.radians(east)
    y_south, y_north = float(mercator_y(south)), float(mercator_y(north))
    width, height = round((x_east - x_west) / pixel), round((y_north - y_south) / pixel)
    xs = x_west + (np.arange(width) + 0.5) * (x_east - x_west) / width
    ys = y_north - (np.arange(height) + 0.5) * (y_north - y_south) / height
    lon = np.degrees(xs / EARTH_WEB)
    lat = np.degrees(2 * np.arctan(np.exp(ys / EARTH_WEB)) - np.pi / 2)
    return np.meshgrid(lat, lon, indexing="ij"), width, height


class Bilinear:
    """Bilinear interpolation from a grid to the output pixels, given each pixel's
    fractional grid position (column fi, row fj). Pixels outside the grid are invalid."""

    def __init__(self, fi, fj, columns, rows):
        self.valid = (fi >= 0) & (fj >= 0) & (fi <= columns - 1) & (fj <= rows - 1)
        fi, fj = np.clip(fi, 0, columns - 1.001), np.clip(fj, 0, rows - 1.001)
        self.i0, self.j0 = np.floor(fi).astype(int), np.floor(fj).astype(int)
        self.ti, self.tj = (fi - self.i0).astype(np.float32), (fj - self.j0).astype(np.float32)

    def __call__(self, values):
        a, b = values[self.j0, self.i0], values[self.j0, self.i0 + 1]
        c, d = values[self.j0 + 1, self.i0], values[self.j0 + 1, self.i0 + 1]
        top = a + (b - a) * self.ti
        bottom = c + (d - c) * self.ti
        return top + (bottom - top) * self.tj


def regular_sampler(lat, lon, lat0, lon0, dlat, dlon, columns, rows):
    """For a regular latitude/longitude grid whose first point is (lat0, lon0)."""
    if lon0 >= 180:
        lon0 -= 360
    return Bilinear((lon - lon0) / dlon, (lat - lat0) / dlat, columns, rows)


def grib_messages(data):
    """The GRIB2 messages in a byte string, one by one (no temporary file)."""
    offset = data.find(b"GRIB")
    while offset >= 0 and offset + 16 <= len(data):
        length = int.from_bytes(data[offset + 8:offset + 16], "big")
        yield data[offset:offset + length]
        offset = data.find(b"GRIB", offset + length)


def direction_from(u, v):
    """Where the wind comes from, degrees, from its east and north components."""
    return np.mod(np.degrees(np.arctan2(-u, -v)), 360)


def save_frame(path, speed, direction, gust, valid=None):
    speed = np.nan_to_num(speed)
    gust = np.maximum(np.nan_to_num(gust), speed)
    channels = [np.clip(np.rint(speed * 5), 0, 255),
                np.mod(np.rint(np.nan_to_num(direction) * 256 / 360), 256),
                np.clip(np.rint(gust * 5), 0, 255)]
    if valid is not None and not valid.all():
        channels.append(np.where(valid, 255, 0))
        image = Image.fromarray(np.stack(channels, axis=-1).astype(np.uint8), "RGBA")
    else:
        image = Image.fromarray(np.stack(channels, axis=-1).astype(np.uint8), "RGB")
    image.save(path, optimize=True)


def save_land(path, land):
    """Land fraction 0-1 (0 = sea) as a grayscale PNG."""
    land = np.clip(np.nan_to_num(land, nan=1.0), 0, 1)
    Image.fromarray(np.rint(land * 255).astype(np.uint8), "L").save(path, optimize=True)


def write_manifest(out, model, source, run_time, bounds, width, height, frames, gust_note="gust m/s x 5"):
    manifest = {
        "model": model,
        "source": source,
        "run": run_time.strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "projection": "Web Mercator (EPSG:3857); the image spans the bounds edge to edge",
        "bounds": dict(zip(("south", "north", "west", "east"), bounds)),
        "width": width, "height": height,
        "encoding": {"red": "wind speed m/s x 5", "green": "direction (from) degrees x 256/360",
                     "blue": gust_note, "alpha": "0 outside the model's area (when present)"},
        "land": "land.png",
        "frames": frames,
    }
    Path(out, "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    return manifest
