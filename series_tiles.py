"""Map taps' series files made here, not on our server (the owner, 18.10.2026: the server's cron made
them beside the stations' collector, sharing its CPU, and the sets are hourly now): after a run of
the Mix or the world mix, the files station-history reads a tapped place's hours from
(series/<set>/<run>/v2/<z>/<x>/<y>.bin, its seriesPoint): for the world mix every tile of its finest
zoom, for the Mix the tiles round the spots and those riders tapped in the last 7 days.

A file: "MGS1", the header's length (4 bytes, little-endian), the header (JSON: {times, size,
blocks: [[offset, length]...]}, offsets from the header's end) and the blocks: the tile in 16 x 16
pixel blocks, row by row, each block every hour's RGBA (hour by hour, its rows top first),
zlib-deflated; a tile with nothing in it has no blocks. Then series/<set>/<run>/done.json.

    python series_tiles.py mix|mix-world [--force] [--local DIR] [--tiles N]
"""
import json
import math
import struct
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

import spot_points
import world_tiles

FILES = spot_points.FILES
WANTED = "https://data.meteogust.com/series/wanted?set={set}"
BLOCK = 16
# The zoom a tap reads (station-history SERIES_ZOOMS): the Mix at 7 where it has it, else 6.
ZOOMS = {"mix": 7}


def tile_at(set_name, index, header, lat, lon):
    """The tile a tap there reads: the set's zoom, else the next coarser one with a tile there."""
    top = index["zooms"][1]
    z = min(top, ZOOMS.get(set_name, top))
    while z >= index["zooms"][0]:
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        if f"{z}/{x}/{y}" in header.tiles:
            return z, x, y
        z -= 1
    return None


def series_file(times, size, pixels):
    """The file's bytes from every hour's RGBA ([hours, size, size, 4], None for an hour without the tile)."""
    if not any(p is not None for p in pixels):
        head = json.dumps({"times": times, "size": size, "blocks": []}, separators=(",", ":")).encode()
        return b"MGS1" + struct.pack("<I", len(head)) + head
    stack = np.zeros((len(pixels), size, size, 4), np.uint8)
    for f, p in enumerate(pixels):
        if p is not None:
            stack[f] = p
    per = math.ceil(size / BLOCK)
    blocks, parts, offset = [], [], 0
    for by in range(per):
        for bx in range(per):
            raw = np.zeros((len(pixels), BLOCK, BLOCK, 4), np.uint8)
            part = stack[:, by * BLOCK:(by + 1) * BLOCK, bx * BLOCK:(bx + 1) * BLOCK]
            raw[:, :part.shape[1], :part.shape[2]] = part
            packed = zlib.compress(raw.tobytes(), 6)
            blocks.append([offset, len(packed)])
            parts.append(packed)
            offset += len(packed)
    head = json.dumps({"times": times, "size": size, "blocks": blocks}, separators=(",", ":")).encode()
    return b"MGS1" + struct.pack("<I", len(head)) + head + b"".join(parts)


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("mix", "mix-world"):
        raise SystemExit(__doc__)
    set_name = args[0]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    limit = int(args[args.index("--tiles") + 1]) if "--tiles" in args else None
    index = json.loads(spot_points.get(FILES + f"tiles/{set_name}/index.json?t={int(time.time())}"))
    run = index["runName"]
    if local is None and "--force" not in args and spot_points.get(FILES + f"series/{set_name}/{run}/done.json"):
        print(f"{set_name} {run}: series made already")
        return
    frames = sorted((f for f in index["frames"] if f.get("file")), key=lambda f: f["time"])
    times = [f["time"] for f in frames]
    started = time.time()
    with ThreadPoolExecutor(16) as pool:
        headers = list(pool.map(lambda f: spot_points.PackReader(FILES + f"tiles/{set_name}/{f['file']}"), frames))
    first = headers[0]
    if set_name == "mix-world":
        z = index["zooms"][1]
        tiles = sorted({tuple(int(v) for v in k.split("/")) for k in first.tiles if int(k.split("/")[0]) == z})
    else:
        wanted = set()
        for spot in spot_points.spots():
            found = tile_at(set_name, index, first, float(spot["lat"]), float(spot["lon"]))
            if found:
                wanted.add(found)
        try:
            for z, x, y in json.loads(spot_points.get(WANTED.format(set=set_name)) or b"[]"):
                if f"{z}/{x}/{y}" in first.tiles:
                    wanted.add((z, x, y))
        except ValueError:
            pass
        tiles = sorted(wanted)
    if limit:
        tiles = tiles[:limit]
    print(f"{set_name} {run}: {len(tiles)} tiles, {len(frames)} hours", flush=True)

    def read(job):
        reader, key = job
        try:
            return reader.tile(*key)
        except Exception:
            return None

    def one(key):
        z = key[0]
        size = first.sizes.get(z, 256)
        with ThreadPoolExecutor(8) as pool:
            pixels = list(pool.map(read, [(h, key) for h in headers]))
        pixels = [p if p is not None and p.shape[0] == size else None for p in pixels]
        data = series_file(times, size, pixels)
        path = f"series/{set_name}/{run}/v2/{z}/{key[1]}/{key[2]}.bin"
        if local:
            (local / path).parent.mkdir(parents=True, exist_ok=True)
            (local / path).write_bytes(data)
        else:
            world_tiles.upload(path, data)
        return len(data)

    # Four tiles at a time (each every hour's tile, about 40 MB while it's made).
    with ThreadPoolExecutor(4) as pool:
        total = sum(pool.map(one, tiles))
    done = json.dumps({"tiles": len(tiles), "hours": len(frames), "bytes": total}).encode()
    if local:
        (local / f"series/{set_name}/{run}").mkdir(parents=True, exist_ok=True)
        (local / f"series/{set_name}/{run}/done.json").write_bytes(done)
    else:
        world_tiles.upload(f"series/{set_name}/{run}/done.json", done)
    print(f"{set_name} {run}: {len(tiles)} series, {total / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
