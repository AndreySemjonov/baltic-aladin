"""City lights at night: NASA's Black Marble as our own tiles, for the app's night look.

At night the app darkens the land and draws the towns' lights over it, a warm glow fading in
with the dusk (the owner, 14.10.2026: as the night pictures of Europe from space). The lights are
NASA's Black Marble (VIIRS, 2016; public domain, through NASA GIBS), read once per zoom from GIBS
and made into grey tiles: 0 dark, 255 a city's bright core, with a soft glow round the lights so
small towns show. The world's zooms 0-5 in one pack (low.pack), Europe's zooms 6-8 in a pack for
each zoom-5 tile with lights (5-x-y.pack), as the coast tiles; dark tiles left out. Made once (the
lights don't change much), uploaded through station-history as tiles/lights/.

    python lights_tiles.py                upload (INGEST_TOKEN)
    python lights_tiles.py --local DIR    write to DIR (a test; --only 18,9 for one zoom-5 tile)
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

import world_tiles

SOURCE = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/2016-01-01/"
          "GoogleMapsCompatible_Level8/{z}/{y}/{x}.png")
SIZE = 256
LOW, BASE, TOP = 5, 5, 8
# The packs' folder (made again: a new one, as they're kept a month in caches; the index names it).
FOLDER = "v2"
# Europe's fine lights (zooms 6-8): the zoom-5 tiles over 25 W to 45 E, 34 to 72 N.
EUROPE_X, EUROPE_Y = range(13, 21), range(6, 13)
# The picture's land is a dim moonlit grey (about 0.06); the lights start a little above it and
# reach the top only in the brightest centres (the owner, 15.10.2026: zoomed in, the Benelux was one
# cream sheet).
FLOOR, SPAN, GLOW = 0.12, 0.75, 1.0


def fetch(z, x, y):
    """One Black Marble tile as brightness 0-1 (its RGB's mean), None when it can't be read."""
    url = SOURCE.format(z=z, x=x, y=y)
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MeteoGust lights (data.meteogust.com)"})
            with urllib.request.urlopen(request, timeout=60) as reply:
                image = Image.open(io.BytesIO(reply.read())).convert("RGB")
            return np.asarray(image, np.float32).mean(axis=2) / 255
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 3:
                return None
        except (urllib.error.URLError, OSError):
            if attempt == 3:
                return None
        time.sleep(5 * (attempt + 1))
    return None


def mosaic(z, x0, y0, columns, rows, pool):
    """The tiles x0.. by y0.. at zoom z as one brightness picture (0 where a tile failed)."""
    keys = [(x0 + i, y0 + j) for j in range(rows) for i in range(columns)]
    out = np.zeros((rows * SIZE, columns * SIZE), np.float32)
    for (x, y), tile in zip(keys, pool.map(lambda k: fetch(z, *k), keys)):
        if tile is not None and tile.shape == (SIZE, SIZE):
            out[(y - y0) * SIZE:(y - y0 + 1) * SIZE, (x - x0) * SIZE:(x - x0 + 1) * SIZE] = tile
    return out


def lights(brightness):
    """Two looks of the lights, 0-255: far (grey: the lights with a soft glow, Europe from space) and
    near (alpha: each place against its surroundings, 3 km round at zoom 8, so towns and their centres
    show apart and the lit spread between them fades; the app blends from far to near as it zooms in)."""
    core = np.clip((brightness - FLOOR) / SPAN, 0, 1)
    far = np.clip(core ** 0.8 * 0.9 + ndimage.gaussian_filter(core ** 0.8, GLOW) * 0.3, 0, 1)
    local = np.clip((core - 0.8 * ndimage.gaussian_filter(core, 4)) * 2.2, 0, 1)
    near = np.clip(local * 0.85 + core * 0.18, 0, 1)
    return np.rint(np.stack([far, near], -1) * 255).astype(np.uint8)


def cut(picture, z, x0, y0, out):
    """The picture's tiles (left out when dark) into `out` as grey PNGs."""
    rows, columns = picture.shape[0] // SIZE, picture.shape[1] // SIZE
    for j in range(rows):
        for i in range(columns):
            tile = picture[j * SIZE:(j + 1) * SIZE, i * SIZE:(i + 1) * SIZE]
            if tile.max() < 8:
                continue
            out[(z, x0 + i, y0 + j)] = world_tiles.png(np.ascontiguousarray(tile), "LA")


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    only = tuple(int(v) for v in args[args.index("--only") + 1].split(",")) if "--only" in args else None
    started = time.time()

    def put(name, data):
        if local:
            local.mkdir(parents=True, exist_ok=True)
            (local / name).write_bytes(data)
        else:
            world_tiles.upload(f"lights/{name}" if name == "index.json" else f"lights/{FOLDER}/{name}", data)

    with ThreadPoolExecutor(16) as pool:
        # The world, zooms 0-5, each from GIBS's own zoom (its averaging keeps the far-out lights).
        low = {}
        if not only:
            for z in range(0, LOW + 1):
                n = 2 ** z
                cut(lights(mosaic(z, 0, 0, n, n, pool)), z, 0, 0, low)
                print(f"zoom {z}: {len(low)} tiles, {time.time() - started:.0f} s", flush=True)
            put("low.pack", world_tiles.encode(low, {z: SIZE for z in range(0, LOW + 1)}))
        # Europe, zooms 6-8, one pack per zoom-5 tile with lights.
        packs, size = [], 0
        for ty in EUROPE_Y:
            for tx in EUROPE_X:
                if only and (tx, ty) != only:
                    continue
                tiles = {}
                for z in range(BASE + 1, TOP + 1):
                    count = 2 ** (z - BASE)
                    cut(lights(mosaic(z, tx * count, ty * count, count, count, pool)), z, tx * count, ty * count, tiles)
                if not tiles:
                    continue
                data = world_tiles.encode(tiles, {z: SIZE for z in range(BASE + 1, TOP + 1)})
                put(f"{BASE}-{tx}-{ty}.pack", data)
                packs.append(f"{tx}-{ty}")
                size += len(data)
            print(f"row {ty}: {len(packs)} packs, {size / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    index = {"source": "NASA Black Marble (VIIRS, 2016; NASA GIBS), public domain", "low": [0, LOW], "zooms": [BASE + 1, TOP],
             "tileSize": SIZE, "encoding": "grey: far (0 dark, 255 a city's core, a soft glow); alpha: near (each place against its 3 km round)",
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "folder": FOLDER, "packs": packs}
    if not only:
        put("index.json", json.dumps(index, separators=(",", ":")).encode())
    print(f"lights: low {len(low)} tiles, {len(packs)} packs, {size / 1e6:.0f} MB, {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
