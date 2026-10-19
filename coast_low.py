"""The coastline far out (the owner, 19.10.2026: zoomed out to the whole world the land wasn't darker,
as it is closer in): tiles/coast/<folder>/low.pack, the zooms 2-4 made from the published zoom-5
tiles (coast_osm.py's, in each 5-x-y.pack; read by range, the header and that one tile), each four
into one: the shore's distance halved with the pixels (the encoding: 127.5 on the shore, 16 levels a
pixel of the tile's zoom, inland up and out to sea down). A zoom-5 tile all land (the index's "land")
is 255, one with no pack all sea. Tiles all sea are left out of the pack.

    python coast_low.py [--local DIR]
"""
import io
import json
import struct
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

import world_tiles

BASE = "https://files.meteogust.com/tiles/coast/"
UA = {"User-Agent": "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"}
SIZE = 256


def get(url, first=None, last=None):
    headers = dict(UA)
    if first is not None:
        headers["Range"] = f"bytes={first}-{last}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as reply:
                return reply.read()
        except OSError:
            if attempt == 3:
                raise


def zoom5_tile(folder, x, y):
    """The zoom-5 tile x, y from its pack (as an array, 0-255), or None."""
    url = f"{BASE}{folder}/5-{x}-{y}.pack"
    start = get(url, 0, 7)
    if start[:4] != b"MGT1":
        return None
    length = struct.unpack("<I", start[4:8])[0]
    head = json.loads(get(url, 8, 8 + length - 1))
    found = head["tiles"].get(f"5/{x}/{y}")
    if not found:
        return None
    offset, size = found
    data = get(url, 8 + length + offset, 8 + length + offset + size - 1)
    image = np.array(Image.open(io.BytesIO(data)))
    return image[..., 0] if image.ndim == 3 else image


def coarser(children):
    """One tile from its four children ({(dx, dy): array or None (sea)}): the 2 x 2 pixels averaged,
    the distance from the shore halved (a pixel twice as wide)."""
    full = np.zeros((2 * SIZE, 2 * SIZE), np.float32)
    for (dx, dy), tile in children.items():
        full[dy * SIZE:(dy + 1) * SIZE, dx * SIZE:(dx + 1) * SIZE] = tile
    mean = full.reshape(SIZE, 2, SIZE, 2).mean(axis=(1, 3))
    # Far from the shore stays far (0 or 255 is "8 pixels or more", not 8: halved it crept to the shore).
    near = np.clip(127.5 + (mean - 127.5) / 2, 0, 255)
    return np.where(mean <= 0.5, 0, np.where(mean >= 254.5, 255, near))


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    index = json.loads(get(BASE + "index.json"))
    folder, packs, land = index["folder"], set(index["packs"]), set(index["land"])
    level = {}
    with ThreadPoolExecutor(16) as pool:
        names = sorted(packs)
        for name, tile in zip(names, pool.map(lambda n: zoom5_tile(folder, *map(int, n.split("-"))), names)):
            if tile is not None and tile.shape == (SIZE, SIZE):
                level[tuple(map(int, name.split("-")))] = tile.astype(np.float32)
    for name in land:
        level[tuple(map(int, name.split("-")))] = np.full((SIZE, SIZE), 255, np.float32)
    print(f"zoom 5: {len(level)} tiles with land", flush=True)
    tiles = {}
    for z in (4, 3, 2):
        upper = {}
        for x, y in {(x // 2, y // 2) for x, y in level}:
            children = {(dx, dy): level.get((2 * x + dx, 2 * y + dy), np.zeros((SIZE, SIZE), np.float32))
                        for dx in (0, 1) for dy in (0, 1)}
            upper[(x, y)] = coarser(children)
        level = upper
        for (x, y), tile in level.items():
            tiles[(z, x, y)] = world_tiles.png(np.round(tile).astype(np.uint8), "L")
        print(f"zoom {z}: {len(level)} tiles", flush=True)
    body = world_tiles.encode(tiles, {z: SIZE for z in (2, 3, 4)})
    if local:
        local.mkdir(parents=True, exist_ok=True)
        (local / "low.pack").write_bytes(body)
    else:
        print(world_tiles.upload(f"coast/{folder}/low.pack", body).strip(), flush=True)
    print(f"low.pack: {len(tiles)} tiles, {len(body) / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
