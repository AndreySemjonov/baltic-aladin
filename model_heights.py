"""Each model's own ground height (the owner, 18.10.2026: the weather mix corrects every model's air
from its ground to the real one, as Open-Meteo does): its grid's HSURF from Open-Meteo's open data
on AWS (data/<model>/static/HSURF.om; the sea -999 there, 0 here), drawn as Web Mercator tiles over
the model's area: tiles/heights/<model>.pack, encoded as tiles/terrain (red x 256 + green: the height
+ 1000 m), alpha 255 on the grid, 0 off it. The grids from each one's meta.json (its projection and
first and last points; the rotated ones as bucket_tiles.py's, checked on the terrain). Made once.

Also (--grids) each grid itself for station-history's spot setup (the owner, 18.10.2026: new spots
off Open-Meteo): tiles/grids/<model>.json, how a place finds its cell (the projection's parameters,
the first point, the steps; ECMWF's Gaussian rows), and tiles/grids/<model>.bin, each cell's ground
as one byte (0 sea, 1 land, 2 off the model's area), zlib, row by row as Open-Meteo has them.

    python model_heights.py [model ...] [--local DIR] [--grids]
"""
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np

import tiler
import world_tiles

BUCKET = "https://openmeteo.s3.amazonaws.com/data/"
ROTATED_CH = "+proj=ob_tran +o_proj=longlat +o_lat_p=43 +o_lon_p=0 +lon_0=10 +R=6371229 +no_defs"
ROTATED_EU = "+proj=ob_tran +o_proj=longlat +o_lat_p=-35 +o_lon_p=0 +lon_0=-188 +R=6371229 +no_defs"
# Open-Meteo's name: how its grid is laid out, the zooms drawn.
MODELS = {
    "ecmwf_ifs": {"kind": "o1280", "zooms": range(0, 6)},
    "ncep_gfs025": {"kind": "latlon", "zooms": range(0, 5)},
    "dwd_icon": {"kind": "latlon", "zooms": range(0, 6)},
    "cmc_gem_gdps": {"kind": "latlon", "zooms": range(0, 5)},
    "dwd_icon_eu": {"kind": "latlon", "zooms": range(2, 7)},
    "metno_nordic_pp": {"kind": "wkt", "zooms": range(3, 7)},
    "dmi_harmonie_arome_europe": {"kind": "wkt", "zooms": range(3, 7)},
    "ukmo_uk_deterministic_2km": {"kind": "wkt", "zooms": range(3, 7)},
    "meteofrance_arome_france_hd": {"kind": "latlon", "zooms": range(3, 7)},
    "meteofrance_arome_france0025": {"kind": "latlon", "zooms": range(3, 7)},
    "dwd_icon_d2": {"kind": "latlon", "zooms": range(3, 7)},
    "knmi_harmonie_arome_netherlands": {"kind": "latlon", "zooms": range(4, 7)},
    "italia_meteo_arpae_icon_2i": {"kind": "latlon", "zooms": range(3, 7)},
    "meteoswiss_icon_ch1": {"kind": "rotated", "proj": ROTATED_CH, "x0": -6.46, "y0": -4.06, "dx": 0.01, "dy": 0.01, "zooms": range(4, 7)},
    "meteoswiss_icon_ch2": {"kind": "rotated", "proj": ROTATED_CH, "x0": -6.46, "y0": -4.06, "dx": 0.02, "dy": 0.02, "zooms": range(4, 7)},
    "knmi_harmonie_arome_europe": {"kind": "rotated", "proj": ROTATED_EU, "x0": 13.5, "y0": 13.6, "dx": -0.05, "dy": -0.05, "zooms": range(2, 7)},
}


def fetch(url):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "baltic-aladin model_heights"})
            with urllib.request.urlopen(request, timeout=300) as reply:
                return reply.read()
        except OSError:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def read_hsurf(model, raw=False):
    import omfiles
    handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
    handle.close()
    Path(handle.name).write_bytes(fetch(BUCKET + f"{model}/static/HSURF.om"))
    reader = omfiles.OmFileReader.from_path(handle.name)
    values = reader.read_array(tuple(slice(None) for _ in reader.shape)).astype(np.float32)
    reader.close()
    os.unlink(handle.name)
    # The sea 0 (raw: -999 as Open-Meteo has it); off the model's own area (ICON-D2's corners) no value.
    if not raw:
        values[np.isfinite(values) & (values < -500)] = 0
    return values


class Grid:
    """A model's grid: place(lon, lat) -> column, row (fractional) and whether it's on the grid."""

    def __init__(self, model, spec, shape):
        meta = json.loads(fetch(BUCKET + f"{model}/static/meta.json"))
        box = [float(v) for v in re.search(r"BBOX\[([^\]]+)\]", meta["crs_wkt"]).group(1).split(",")]
        self.ny, self.nx = shape[-2], shape[-1]
        self.kind = spec["kind"]
        self.to_grid = None
        if self.kind == "latlon":
            # Rows south to north from the first point, columns east from it (Open-Meteo's order).
            self.x0, self.y0 = box[1], box[0]
            self.dx, self.dy = (box[3] - box[1]) / (self.nx - 1), (box[2] - box[0]) / (self.ny - 1)
        elif self.kind == "rotated":
            from pyproj import CRS, Transformer
            self.to_grid = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_proj4(spec["proj"]), always_xy=True)
            self.x0, self.y0, self.dx, self.dy = spec["x0"], spec["y0"], spec["dx"], spec["dy"]
        elif self.kind == "wkt":
            from pyproj import CRS, Transformer
            self.to_grid = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_wkt(meta["crs_wkt"]), always_xy=True)
            (xa, xb), (ya, yb) = self.to_grid.transform([box[1], box[3]], [box[0], box[2]])
            self.x0, self.y0 = xa, ya
            self.dx, self.dy = (xb - xa) / (self.nx - 1), (yb - ya) / (self.ny - 1)
        # The model's area for the tiles: its edges' latitudes and longitudes.
        cols = np.concatenate([np.arange(self.nx), np.arange(self.nx), np.zeros(self.ny), np.full(self.ny, self.nx - 1)])
        rows = np.concatenate([np.zeros(self.nx), np.full(self.nx, self.ny - 1), np.arange(self.ny), np.arange(self.ny)])
        lon, lat = self.lonlat(cols, rows)
        self.box = (float(np.min(lat)), float(np.max(lat)), float(np.min(lon)), float(np.max(lon)))

    def lonlat(self, cols, rows):
        x, y = self.x0 + cols * self.dx, self.y0 + rows * self.dy
        if self.to_grid is None:
            return x, y
        return self.to_grid.transform(x, y, direction="INVERSE")

    def place(self, lon, lat):
        if self.kind == "latlon":
            x = lon
            if self.x0 + (self.nx - 1) * self.dx > 179:  # a world grid: longitudes from its first
                x = (lon - self.x0) % 360 + self.x0
            col, row = (x - self.x0) / self.dx, (lat - self.y0) / self.dy
        else:
            x, y = self.to_grid.transform(lon, lat)
            col, row = (np.asarray(x) - self.x0) / self.dx, (np.asarray(y) - self.y0) / self.dy
        col, row = np.asarray(col, dtype=float), np.asarray(row, dtype=float)
        world = self.kind == "latlon" and self.nx * self.dx > 359
        on = (row >= 0) & (row <= self.ny - 1) & (((col >= 0) & (col <= self.nx - 1)) | world)
        return col, row, on


def bilinear(values, col, row, wrap):
    ny, nx = values.shape
    if wrap:
        values = np.concatenate([values, values[:, :1]], axis=1)
        nx += 1
    c = np.clip(col, 0, nx - 1.001)
    r = np.clip(row, 0, ny - 1.001)
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
    fc, fr = c - c0, r - r0
    return (values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc) * (1 - fr) + (values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc) * fr


def o1280_regular(values):
    """ECMWF's O1280 points (north first) on a 0.1° grid (rows north to south) by the 4 nearest."""
    from scipy.spatial import cKDTree
    x, _ = np.polynomial.legendre.leggauss(2560)
    lats = np.degrees(np.arcsin(x))[::-1]
    counts = np.array([20 + 4 * i for i in range(1280)] + [20 + 4 * (2559 - i) for i in range(1280, 2560)])
    la = np.radians(np.repeat(lats, counts))
    lo = np.radians(np.concatenate([np.arange(n) * 360.0 / n for n in counts]))
    tree = cKDTree(np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=1))
    glat = np.radians(90 - np.arange(1801) * 0.1)
    glon = np.radians(np.arange(3600) * 0.1)
    gl, go = np.meshgrid(glat, glon, indexing="ij")
    points = np.stack([np.cos(gl) * np.cos(go), np.cos(gl) * np.sin(go), np.sin(gl)], axis=-1).reshape(-1, 3)
    distance, nearest = tree.query(points, k=4, workers=-1)
    share = 1 / np.maximum(distance, 1e-9)
    share /= share.sum(axis=1, keepdims=True)
    return (values.ravel()[nearest] * share).sum(axis=1).reshape(1801, 3600).astype(np.float32)


def tile_rgba(height, on):
    value = np.clip(np.rint(height) + 1000, 0, 65535).astype(np.uint32)
    rgba = np.stack([value >> 8, value & 255, np.zeros_like(value), np.where(on, 255, 0)], axis=-1)
    return world_tiles.png(rgba.astype(np.uint8), "RGBA")


def make(model, local):
    spec = MODELS[model]
    started = time.time()
    values = read_hsurf(model)
    if spec["kind"] == "o1280":
        grid_values = o1280_regular(values)

        def heights(lon, lat):
            col, row = (np.asarray(lon) % 360) / 0.1, (90 - np.asarray(lat)) / 0.1
            return bilinear(grid_values, col, row, True), np.ones(np.shape(col), bool)
        box = (-85.06, 85.06, -180, 180)
    else:
        grid = Grid(model, spec, values.shape)
        wrap = grid.kind == "latlon" and grid.nx * grid.dx > 359

        def heights(lon, lat):
            col, row, on = grid.place(lon, lat)
            return bilinear(values, col, row, wrap), on
        box = (max(grid.box[0], -85.06), min(grid.box[1], 85.06), grid.box[2], grid.box[3])
    tiles = {}
    for z, x, y in tiler.tiles_in_box(box, spec["zooms"]):
        n = 2 ** z
        px = (x * 256 + np.arange(256) + 0.5) / (n * 256)
        py = (y * 256 + np.arange(256) + 0.5) / (n * 256)
        lon = np.broadcast_to(px * 360 - 180, (256, 256))
        lat = np.broadcast_to(np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py))))[:, None], (256, 256))
        h, on = heights(lon.ravel(), lat.ravel())
        h = np.asarray(h).reshape(256, 256)
        on = np.asarray(on).reshape(256, 256) & np.isfinite(h)
        h = np.nan_to_num(h)
        if on.any():
            tiles[(z, x, y)] = tile_rgba(h, on)
    data = world_tiles.encode(tiles, {z: 256 for z in spec["zooms"]})
    if local:
        (local / "heights").mkdir(parents=True, exist_ok=True)
        (local / "heights" / f"{model}.pack").write_bytes(data)
    else:
        world_tiles.upload(f"heights/{model}.pack", data)
    print(f"{model}: {len(tiles)} tiles, {len(data) / 1e6:.1f} MB, {time.time() - started:.0f} s", flush=True)


def grid_files(model, local):
    """The grid's description and its land and sea, for the server's nearest cell (as Open-Meteo:
    plain degrees on regular grids and ECMWF's own, the nearest column and row on projected ones)."""
    import zlib
    spec = MODELS[model]
    values = read_hsurf(model, raw=True)
    flat = values.ravel()
    mask = np.where(~np.isfinite(flat), 2, np.where(flat < -500, 0, 1)).astype(np.uint8)
    if spec["kind"] == "o1280":
        x, _ = np.polynomial.legendre.leggauss(2560)
        lats = np.degrees(np.arcsin(x))[::-1]
        info = {"kind": "o1280", "lats": [round(float(v), 6) for v in lats]}
    else:
        grid = Grid(model, spec, values.shape)
        info = {"kind": grid.kind, "x0": grid.x0, "y0": grid.y0, "dx": grid.dx, "dy": grid.dy, "nx": grid.nx, "ny": grid.ny}
        if grid.kind == "rotated":
            parts = dict(item.lstrip("+").split("=", 1) for item in spec["proj"].split() if "=" in item)
            info.update(kind="rotated", o_lat_p=float(parts["o_lat_p"]), lon_0=float(parts["lon_0"]))
        elif grid.kind == "wkt":
            params = grid.to_grid.target_crs.to_dict()
            info.update(kind=params["proj"], lat_0=float(params["lat_0"]), lon_0=float(params["lon_0"]))
        elif grid.kind == "latlon":
            info["wrap"] = bool(grid.nx * grid.dx > 359)
    body = json.dumps(info, separators=(",", ":")).encode()
    packed = zlib.compress(mask.tobytes(), 9)
    if local:
        (local / "grids").mkdir(parents=True, exist_ok=True)
        (local / "grids" / f"{model}.json").write_bytes(body)
        (local / "grids" / f"{model}.bin").write_bytes(packed)
    else:
        world_tiles.upload(f"grids/{model}.bin", packed)
        world_tiles.upload(f"grids/{model}.json", body)
    print(f"{model} grid: {info['kind']}, {mask.size} cells, {len(packed) / 1e3:.0f} kB", flush=True)


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    chosen = [a for a in args if a in MODELS] or list(MODELS)
    failed = []
    for model in chosen:
        try:
            if "--grids" in args:
                grid_files(model, local)
            else:
                make(model, local)
        except Exception as error:
            failed.append(model)
            print(f"{model} failed: {error}", flush=True)
    if failed:
        raise SystemExit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
