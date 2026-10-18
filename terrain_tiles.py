"""Our terrain heights (the owner, 18.10.2026: the weather's temperature corrected to a place's height,
as Open-Meteo does, and new spots' heights off Open-Meteo): Copernicus GLO-90 (90 m; Copernicus DEM,
free with credit) from Open-Meteo's open data on AWS (data/copernicus_dem90/static/lat_<n>.om: one
degree of latitude each, 1200 rows south to north; 432000 columns to 50°, fewer towards the poles;
the sea 0), as Web Mercator tiles of each pixel's mean height and land share, zooms 0-9:

    tiles/terrain/low.pack        zooms 0-5
    tiles/terrain/5-x-y.pack      zooms 6-9 under that zoom-5 tile, where there's land
    tiles/terrain/index.json      {"packs": ["x-y", ...], "zooms": [0, 9], "encoding": ...}

Each tile RGBA: red and green the height + 1000 m as one 16-bit number (red the high byte), blue the
land's share x 255, alpha 255. Made once (the data doesn't change); about 30-40 minutes.

    python terrain_tiles.py [--local DIR] [--rows A,B]   (--rows: zoom-9 tile rows, a test)
"""
import json
import math
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np

import world_tiles

BUCKET = "https://openmeteo.s3.amazonaws.com/data/copernicus_dem90/static/"
TOP, SIZE, LOW = 9, 256, 5
N9 = 2 ** TOP * SIZE
BLOCK = 120  # DEM rows read at a time


def lat_of(y, n):
    """The latitude of global pixel row edge y at n pixels round."""
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


class Dem:
    """The bands, each fetched when first needed (two kept on disk: a pixel row can span two) and
    read BLOCK rows at a time."""

    def __init__(self):
        self.open_bands = {}  # band: (reader, path, (start, data))
        self.colmaps = {}

    def open(self, band):
        import omfiles
        if band in self.open_bands:
            return self.open_bands[band]
        for old in [b for b in self.open_bands if abs(b - band) > 1]:
            reader, path, _ = self.open_bands.pop(old)
            reader.close()
            os.unlink(path)
        handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
        handle.close()
        for attempt in range(5):
            try:
                request = urllib.request.Request(BUCKET + f"lat_{band}.om", headers={"User-Agent": "baltic-aladin terrain_tiles"})
                with urllib.request.urlopen(request, timeout=600) as reply, open(handle.name, "wb") as out:
                    while True:
                        chunk = reply.read(1 << 22)
                        if not chunk:
                            break
                        out.write(chunk)
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(10 * (attempt + 1))
        self.open_bands[band] = [omfiles.OmFileReader.from_path(handle.name), handle.name, (None, None)]
        return self.open_bands[band]

    def rows(self, band, r0, r1):
        """Rows r0..r1-1 of a band (r1 - r0 <= BLOCK), float32, and the band's column map to pixels."""
        entry = self.open(band)
        reader = entry[0]
        cols = reader.shape[1]
        start, data = entry[2]
        if start is None or r0 < start or r1 > start + BLOCK:
            start = max(0, min(r0 - BLOCK + (r1 - r0), 1200 - BLOCK))
            data = reader.read_array((slice(start, start + BLOCK), slice(0, cols))).astype(np.float32)
            entry[2] = (start, data)
        if cols not in self.colmaps:
            self.colmaps[cols] = np.minimum(((np.arange(cols) + 0.5) / cols * N9).astype(np.int64), N9 - 1)
        return data[r0 - start:r1 - start], self.colmaps[cols]


def pixel_row(dem, y):
    """Global zoom-9 pixel row y: each pixel's mean height and land share."""
    north, south = lat_of(y, N9), lat_of(y + 1, N9)
    # The DEM rows whose centres fall in the row (at least the nearest one).
    picks = []
    for band in range(math.floor(south), math.floor(north) + 1):
        lo = max(0, math.ceil((south - band) * 1200 - 0.5))
        hi = min(1200, math.floor((north - band) * 1200 - 0.5) + 1)
        if hi > lo:
            picks.append((band, lo, hi))
    if not picks:
        middle = (north + south) / 2
        band = math.floor(middle)
        r = min(1199, max(0, int((middle - band) * 1200)))
        picks = [(band, r, r + 1)]
    total = np.zeros(N9)
    land = np.zeros(N9)
    count = np.zeros(N9)
    for band, lo, hi in picks:
        for a in range(lo, hi, BLOCK):
            rows, colmap = dem.rows(band, a, min(hi, a + BLOCK))
            k = rows.shape[0]
            total += np.bincount(colmap, weights=rows.sum(axis=0), minlength=N9)
            land += np.bincount(colmap, weights=(rows != 0).sum(axis=0), minlength=N9)
            count += np.bincount(colmap, minlength=N9) * k
    have = count > 0
    if not have.all():
        idx = np.arange(N9)
        total = np.interp(idx, idx[have], (total[have] / count[have])) * np.maximum(count, 1)
        land = np.interp(idx, idx[have], (land[have] / count[have])) * np.maximum(count, 1)
        count = np.maximum(count, 1)
    return (total / count).astype(np.float32), (land / count).astype(np.float32)


def tile_png(height, land):
    value = np.clip(np.rint(height) + 1000, 0, 65535).astype(np.uint32)
    rgba = np.stack([value >> 8, value & 255, np.clip(np.rint(land * 255), 0, 255), np.full(value.shape, 255)], axis=-1)
    return world_tiles.png(rgba.astype(np.uint8), "RGBA")


class Pyramid:
    """Each zoom's tile row filled from the one under it, two rows at a time; finished tiles to their packs."""

    def __init__(self, emit):
        self.emit = emit
        self.rows = {z: (np.zeros((SIZE, 2 ** z * SIZE), np.float32), np.zeros((SIZE, 2 ** z * SIZE), np.float32)) for z in range(TOP)}

    def finish(self, z, ty, height, land):
        """Zoom z's tile row ty is done: its tiles out, its half of the row above in."""
        for tx in range(2 ** z):
            h = height[:, tx * SIZE:(tx + 1) * SIZE]
            g = land[:, tx * SIZE:(tx + 1) * SIZE]
            if g.max() > 0 or z <= LOW:
                self.emit(z, tx, ty, tile_png(h, g))
        if z == 0:
            return
        up_h, up_l = self.rows[z - 1]
        half = (ty % 2) * (SIZE // 2)
        up_h[half:half + SIZE // 2] = height.reshape(SIZE // 2, 2, -1, 2).mean(axis=(1, 3))
        up_l[half:half + SIZE // 2] = land.reshape(SIZE // 2, 2, -1, 2).mean(axis=(1, 3))
        if ty % 2 == 1:
            self.finish(z - 1, ty // 2, up_h, up_l)


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    rows = [int(v) for v in args[args.index("--rows") + 1].split(",")] if "--rows" in args else [0, 2 ** TOP]
    started = time.time()
    dem = Dem()
    low, packs, names, total = {}, {}, [], [0]

    def put(name, data):
        total[0] += len(data)
        if local:
            (local / "terrain").mkdir(parents=True, exist_ok=True)
            (local / "terrain" / name).write_bytes(data)
        else:
            world_tiles.upload(f"terrain/{name}", data)

    def emit(z, x, y, png):
        if z <= LOW:
            low[(z, x, y)] = png
        else:
            f = 2 ** (z - LOW)
            packs.setdefault((x // f, y // f), {})[(z, x, y)] = png

    def flush(y5):
        """The zoom-5 row y5's packs (its zooms 6-9 are all done once its last zoom-9 row is)."""
        for (x5, yy) in [k for k in packs if k[1] == y5]:
            tiles = packs.pop((x5, yy))
            put(f"5-{x5}-{yy}.pack", world_tiles.encode(tiles, {z: SIZE for z in range(LOW + 1, TOP + 1)}))
            names.append(f"{x5}-{yy}")

    pyramid = Pyramid(emit)
    height = np.zeros((SIZE, N9), np.float32)
    land = np.zeros((SIZE, N9), np.float32)
    for ty in range(*rows):
        for py in range(SIZE):
            height[py], land[py] = pixel_row(dem, ty * SIZE + py)
        pyramid.finish(TOP, ty, height, land)
        if ty % (2 ** (TOP - LOW)) == 2 ** (TOP - LOW) - 1:
            flush(ty // 2 ** (TOP - LOW))
        print(f"  row {ty}: {lat_of(ty * SIZE, N9):.1f}°, {len(names)} packs, {total[0] / 1e9:.2f} GB, {time.time() - started:.0f} s", flush=True)
    for y5 in sorted({k[1] for k in packs}):
        flush(y5)
    if rows == [0, 2 ** TOP]:
        put("low.pack", world_tiles.encode(low, {z: SIZE for z in range(LOW + 1)}))
    index = {"source": "Copernicus GLO-90 DEM (Copernicus, ESA), via Open-Meteo's open data on AWS",
             "zooms": [0, TOP], "low": [0, LOW], "packs": sorted(names),
             "encoding": "red x 256 + green: height + 1000 m (the pixel's mean); blue: land share x 255"}
    put("index.json", json.dumps(index, separators=(",", ":")).encode())
    print(f"terrain: {len(names)} packs, {total[0] / 1e9:.2f} GB, {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
