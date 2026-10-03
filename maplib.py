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


# ---------- The coast ----------
#
# A model knows land and sea only per grid cell (1-25 km), so near the shore its weak land
# wind leaks out over the sea in squares. With the fine coastline (coast.png, about 250 m,
# from OpenStreetMap; build_coast.py), sea pixels within about 1.5 model cells of the land
# take the values of the nearest sea pixel further out, where the model is clearly over the
# sea; land pixels keep theirs. The sea wind then reaches the real shoreline.

HERE = Path(__file__).parent
_coast = {}


def land_share(lat, lon):
    """The share of land (0-1) in each output pixel from the fine coastline; NaN outside
    its box, None without coast.png."""
    if "fine" not in _coast:
        try:
            meta = json.loads((HERE / "coast.json").read_text(encoding="utf-8"))
            fine = np.asarray(Image.open(HERE / "coast.png").convert("L")) > 127
            _coast["fine"] = (meta["bounds"], np.pad(fine.cumsum(0).cumsum(1), ((1, 0), (1, 0))).astype(np.int64))
        except (OSError, ValueError, KeyError):
            _coast["fine"] = None
    if _coast["fine"] is None:
        return None
    b, total = _coast["fine"]
    rows, columns = total.shape[0] - 1, total.shape[1] - 1
    lons, lats = lon[0, :], lat[:, 0]
    # Pixel edges: the output grid is regular in longitude and in Mercator y.
    dx = (lons[-1] - lons[0]) / max(len(lons) - 1, 1)
    ys = mercator_y(lats)
    dy = (ys[0] - ys[-1]) / max(len(ys) - 1, 1)
    top, bottom = float(mercator_y(b["north"])), float(mercator_y(b["south"]))
    c0 = np.floor((lons - dx / 2 - b["west"]) / (b["east"] - b["west"]) * columns).astype(int)
    c1 = np.ceil((lons + dx / 2 - b["west"]) / (b["east"] - b["west"]) * columns).astype(int)
    r0 = np.floor((top - (ys + dy / 2)) / (top - bottom) * rows).astype(int)
    r1 = np.ceil((top - (ys - dy / 2)) / (top - bottom) * rows).astype(int)
    inside_c = (c0 >= 0) & (c1 <= columns)
    inside_r = (r0 >= 0) & (r1 <= rows)
    c0, c1 = np.clip(c0, 0, columns), np.clip(c1, 0, columns)
    r0, r1 = np.clip(r0, 0, rows), np.clip(r1, 0, rows)
    R0, C0 = np.meshgrid(r0, c0, indexing="ij")
    R1, C1 = np.meshgrid(r1, c1, indexing="ij")
    count = np.maximum((R1 - R0) * (C1 - C0), 1)
    land = (total[R1, C1] - total[R0, C1] - total[R1, C0] + total[R0, C0]) / count
    return np.where(np.outer(inside_r, inside_c), land, np.nan).astype(np.float32)


# Off since 3.10.2026: the filled sea ended in a cliff at the shoreline, while the models' own
# fields (MET Nordic 1 km above all) fall off gradually over a few km, as on Windguru's maps.
# The fine coastline is still used for the maps' land.png (the app's land shading).
SEA_FILL = False


def coast_fill(arrays, share, buffer_km, km_per_pixel, valid=None):
    """Sea pixels within `buffer_km` of the land (by `share`) take, in each of `arrays`
    (changed in place), the value of the nearest sea pixel beyond it; only from pixels with
    model data, and not from further than twice the buffer (a lagoon keeps its own)."""
    if not SEA_FILL or share is None or buffer_km <= 0:
        return arrays
    from scipy import ndimage
    key = (id(share), buffer_km, km_per_pixel, None if valid is None else valid.tobytes())
    if key not in _coast:
        buffer = buffer_km / km_per_pixel
        known = np.isfinite(share)
        land = known & (share >= 0.5)
        sea = known & ~land
        from_land = ndimage.distance_transform_edt(~land)
        source = sea & (from_land > buffer + 0.5)
        if valid is not None:
            source &= valid
        if not source.any():
            _coast[key] = None
        else:
            distance, (ri, ci) = ndimage.distance_transform_edt(~source, return_indices=True)
            target = sea & ~source & (distance <= 2 * buffer + 1)
            if valid is not None:
                target &= valid
            _coast[key] = (target, ri[target], ci[target])
    found = _coast[key]
    if found is not None:
        target, ri, ci = found
        for values in arrays:
            values[target] = values[ri, ci]
    return arrays


def save_frame(path, speed, direction, gust, valid=None, coast=None):
    """`coast`: (share, buffer_km, km_per_pixel) to fill the sea near the shore first."""
    if coast is not None:
        speed, direction, gust = (np.array(np.nan_to_num(a), dtype=np.float32) for a in (speed, direction, gust))
        coast_fill([speed, direction, gust], *coast, valid=valid)
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
