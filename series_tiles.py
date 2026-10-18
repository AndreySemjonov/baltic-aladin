"""The places' files made here, not on our server (the owner, 18.10.2026: the server's cron made
them beside the stations' collector, sharing its CPU; and "why does a new place take so long"): after
each run of a wind set, the files station-history reads a place's hours from (a map tap, a rider's
own spot, its seriesPoint: series/<set>/<run>/v2/<z>/<x>/<y>.bin), for every tile of the set's area at
the zoom a tap reads (SERIES_ZOOMS; the next coarser where the set has no tile at it): the map's
hourly packs turned round, every hour of a place together, as Open-Meteo keeps its data. Then
series/<set>/<run>/done.json and series/<set>/runs.json (this run and the one before, read while the
next is made).

A file: "MGS1", the header's length (4 bytes, little-endian), the header (JSON: {times, size,
blocks: [[offset, length]...]}, offsets from the header's end) and the blocks: the tile in 16 x 16
pixel blocks, row by row, each block every hour's RGBA (hour by hour, its rows top first),
zlib-deflated; a tile with nothing in it has no blocks. Then series/<set>/<run>/done.json.

    python series_tiles.py [set ...] [--force] [--local DIR] [--tiles N]   (no set: every one, each when new)
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
BLOCK = 16
# The zoom a tap reads (station-history SERIES_ZOOMS, the same): the 1 km models and the Mix at 7,
# the 2-2.5 km ones at 6, the others their finest.
ZOOMS = {"mix": 7, "metnordic-1km": 7, "arome": 7, "icon-ch1": 7, "icon-d2": 6, "dini": 6, "harm-nl": 6, "icon-2i": 6,
         "harm-fi": 6, "aladin": 6, "ukmo": 6, "icon-ch2": 6, "arome25": 6}
SETS = ["mix", "mix-world", "metnordic-1km", "dini", "arome", "ukmo", "icon-ch2", "arome25", "knmi-eu", "harm-nl", "icon-d2", "icon-ch1", "icon-2i", "harm-fi", "aladin",
        "icon-eu", "ecmwf", "gfs", "icon", "gdps",
        # The weather's and the waves' too (the owner, 18.10.2026: a place's weather and waves from our
        # files, any place): "<folder>:<layer>", named "<folder>-<layer>" (the waves their own sets).
        "ecmwf:temp", "ecmwf:rain", "ecmwf:cloud", "ecmwf:sky", "icon-eu-wx:temp", "icon-eu-wx:rain", "icon-eu-wx:cloud",
        "metnordic-1km-wx:temp", "metnordic-1km-wx:rain", "gfs:temp", "gfs:rain", "icon:temp", "icon:rain",
        "gdps:temp", "gdps:rain", "ewam", "ecmwf-waves"]


def area_tiles(set_name, index, header):
    """Every tile a tap in the set's area reads: those at its zoom, and the next coarser zoom's where
    it has none of their four there."""
    top = index["zooms"][1]
    z = min(top, ZOOMS.get(set_name, 7 if set_name.startswith("wrf-") else top))
    keys = {tuple(int(v) for v in k.split("/")) for k in header.tiles}
    tiles = {k for k in keys if k[0] == z}
    if z > index["zooms"][0]:
        for k in keys:
            if k[0] == z - 1 and not any((z, 2 * k[1] + dx, 2 * k[2] + dy) in tiles for dx in (0, 1) for dy in (0, 1)):
                tiles.add(k)
    return sorted(tiles)


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


def make(spec, local, limit, force):
    # A wind set, or "<folder>:<layer>" (a weather layer's packs: the frames' "temp", "rain", "cloud").
    folder, layer = spec.split(":", 1) if ":" in spec else (spec, "file")
    set_name = folder if layer == "file" else f"{folder}-{layer}"
    index_data = spot_points.get(FILES + f"tiles/{folder}/index.json?t={int(time.time())}")
    if not index_data:
        print(f"{set_name}: no index", flush=True)
        return
    index = json.loads(index_data)
    run = index["runName"]
    if local is None and not force and spot_points.get(FILES + f"series/{set_name}/{run}/done.json"):
        print(f"{set_name} {run}: series made already", flush=True)
        return
    frames = sorted((f for f in index["frames"] if f.get(layer)), key=lambda f: f["time"])
    if not frames:
        print(f"{set_name} {run}: no {layer} packs", flush=True)
        return
    times = [f["time"] for f in frames]
    started = time.time()
    with ThreadPoolExecutor(16) as pool:
        headers = list(pool.map(lambda f: spot_points.PackReader(FILES + f"tiles/{folder}/{f[layer]}"), frames))
    # The first hour's pack: the widest (regional models end sooner).
    first = headers[0]
    tiles = area_tiles(set_name, index, first)
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
        # This run and the one before (station-history reads the one before while the next is made).
        before = spot_points.get(FILES + f"series/{set_name}/runs.json?t={int(time.time())}")
        try:
            runs = [r for r in json.loads(before)["runs"] if r != run][:1] if before else []
        except (ValueError, KeyError, TypeError):
            runs = []
        world_tiles.upload(f"series/{set_name}/runs.json", json.dumps({"runs": [run] + runs}).encode())
    print(f"{set_name} {run}: {len(tiles)} series, {total / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    limit = int(args[args.index("--tiles") + 1]) if "--tiles" in args else None
    values = {args[i + 1] for i, a in enumerate(args[:-1]) if a in ("--local", "--tiles")}
    chosen = [a for a in args if not a.startswith("--") and a not in values]
    for set_name in chosen or SETS:
        try:
            make(set_name, local, limit, "--force" in args)
        except Exception as error:
            print(f"{set_name}: {error}", flush=True)


if __name__ == "__main__":
    main()
