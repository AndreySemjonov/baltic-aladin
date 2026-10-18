"""The world's lakes as a water mask (the owner, 18.10.2026: off Open-Meteo, step 7): a new spot on a
lake found no water in ours (our terrain's land share and the models' ground know only the sea: a lake
keeps its surface height, Lake Garda's 65 m), so its open-water side and each model's first water cell
came from Open-Meteo. From HydroLAKES (Messager et al. 2016, CC BY 4.0; the lakes from 10 ha, as our
map's lakes, water_tiles.py), each pixel's lake share on the web map's grid, zooms 6-10 (zoom 10: about
150 m at the equator, 80 m at 57 N).

    tiles/lakes/5-x-y.pack   zooms 6-10 under that zoom-5 tile, where it has a lake (all-dry tiles left out)
    tiles/lakes/index.json   {"packs": ["x-y", ...], "zooms": [6, 10], ...}

A tile: a grey PNG, the lake's share of each pixel x 255 (zoom 10: in or out by the pixel's centre; the
coarser zooms their four pixels' mean). Made once (by hand, lakes.yml); station-history's /elevation and
/cells read it (their "lake" answers).

    python lakes_tiles.py [--local DIR] [--work DIR] [--only x5-y5]
"""
import json
import math
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

import world_tiles
from water_tiles import LAKES, download

LOW, TOP, SIZE = 5, 10, 256
SPAN = SIZE * 2 ** (TOP - LOW)
N = SIZE * 2 ** TOP
LIMIT = 85.0511
GPKG = None


def lat_of(y, n):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def tile_x(lon, n):
    return np.floor((np.asarray(lon) + 180) / 360 * n).astype(int)


def tile_y(lat, n):
    lat = np.radians(np.clip(np.asarray(lat), -LIMIT, LIMIT))
    return np.floor((1 - np.arcsinh(np.tan(lat)) / math.pi) / 2 * n).astype(int)


def wanted_tiles(gpkg):
    """The zoom-5 tiles any lake's box touches."""
    import pyogrio
    _, bounds = pyogrio.read_bounds(gpkg)
    n = 2 ** LOW
    x0, x1 = np.clip(tile_x(bounds[0], n), 0, n - 1), np.clip(tile_x(bounds[2], n), 0, n - 1)
    y0, y1 = np.clip(tile_y(bounds[3], n), 0, n - 1), np.clip(tile_y(bounds[1], n), 0, n - 1)
    out = set()
    for a, b, c, d in zip(x0, x1, y0, y1):
        for x in range(a, b + 1):
            for y in range(c, d + 1):
                out.add((int(x), int(y)))
    return sorted(out)


def make_pack(job):
    """One zoom-5 tile's pack ({(z, x, y): PNG} encoded), or None when no pixel is a lake."""
    gpkg, x5, y5 = job
    import pyogrio
    import shapely
    from affine import Affine
    from rasterio import features
    lon0, lon1 = x5 / 2 ** LOW * 360 - 180, (x5 + 1) / 2 ** LOW * 360 - 180
    top, bottom = lat_of(y5, 2 ** LOW), lat_of(y5 + 1, 2 ** LOW)
    _, _, geometry, _ = pyogrio.raw.read(gpkg, bbox=(lon0, bottom, lon1, top), columns=[], read_geometry=True)
    if geometry is None or not len(geometry):
        return None
    shapes = shapely.from_wkb(geometry)

    def to_pixels(coords):
        lon = coords[:, 0]
        lat = np.radians(np.clip(coords[:, 1], -LIMIT, LIMIT))
        x = (lon + 180) / 360 * N - x5 * SPAN
        y = (1 - np.arcsinh(np.tan(lat)) / math.pi) / 2 * N - y5 * SPAN
        return np.stack([x, y], axis=1)

    shapes = shapely.transform(shapes, to_pixels)
    mask = features.rasterize(((s, 255) for s in shapes if s is not None and not s.is_empty), out_shape=(SPAN, SPAN),
                              transform=Affine.identity(), fill=0, dtype="uint8")
    if not mask.any():
        return None
    tiles = {}
    level = mask.astype(np.float32)
    for z in range(TOP, LOW, -1):
        per = 2 ** (z - LOW)
        for j in range(per):
            for i in range(per):
                part = level[j * SIZE:(j + 1) * SIZE, i * SIZE:(i + 1) * SIZE]
                if part.max() <= 0:
                    continue
                tiles[(z, x5 * per + i, y5 * per + j)] = world_tiles.png(np.clip(np.rint(part), 0, 255).astype(np.uint8), "L")
        size = level.shape[0] // 2
        level = level.reshape(size, 2, size, 2).mean(axis=(1, 3))
    return world_tiles.encode(tiles, {z: SIZE for z in range(LOW + 1, TOP + 1)})


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    work = Path(args[args.index("--work") + 1]) if "--work" in args else Path("lakes-work")
    only = args[args.index("--only") + 1] if "--only" in args else None
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    zipped = work / "HydroLAKES_polys_v10_shp.zip"
    download(LAKES, zipped)
    shp = next(work.rglob("HydroLAKES_polys_v10.shp"), None)
    if shp is None:
        with zipfile.ZipFile(zipped) as archive:
            archive.extractall(work)
        shp = next(work.rglob("HydroLAKES_polys_v10.shp"))
    gpkg = work / "lakes.gpkg"
    if not gpkg.exists():
        # A GeoPackage: its spatial index makes each tile's read quick.
        subprocess.run(["ogr2ogr", "-f", "GPKG", str(gpkg), str(shp), "-select", "Hylak_id", "-nln", "lakes",
                        "-nlt", "PROMOTE_TO_MULTI", "-lco", "SPATIAL_INDEX=YES"], check=True)
    print(f"lakes ready: {time.time() - started:.0f} s", flush=True)
    tiles = wanted_tiles(str(gpkg))
    if only:
        tiles = [tuple(int(v) for v in only.split("-"))]
    print(f"{len(tiles)} zoom-5 tiles with lakes", flush=True)

    def put(name, data):
        if local:
            (local / "lakes").mkdir(parents=True, exist_ok=True)
            (local / "lakes" / name).write_bytes(data)
        else:
            world_tiles.upload(f"lakes/{name}", data)

    names, total = [], 0
    with ProcessPoolExecutor(4) as pool:
        for (x5, y5), data in zip(tiles, pool.map(make_pack, [(str(gpkg), x, y) for x, y in tiles])):
            if data is None:
                continue
            put(f"5-{x5}-{y5}.pack", data)
            names.append(f"{x5}-{y5}")
            total += len(data)
            if len(names) % 25 == 0:
                print(f"  {len(names)} packs, {total / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    if not only:
        index = {"source": "HydroLAKES (Messager et al. 2016, CC BY 4.0), lakes from 10 ha", "zooms": [LOW + 1, TOP],
                 "packs": sorted(names), "encoding": "grey: the pixel's lake share x 255"}
        put("index.json", json.dumps(index, separators=(",", ":")).encode())
    print(f"lakes: {len(names)} packs, {total / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
