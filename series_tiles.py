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

Since 21.10.2026 (the owner, "W1": one R2 write per file, the series were ~54k of ~95k writes a day) the tiles'
files go into packs of up to PACK_BYTES: series/<set>/<run>/v3/pack-<n>.bin, each tile's file as it was, one after
another; done.json then says where each is: {"packs": ["v3/pack-0.bin", ...], "at": {"z/x/y": [pack, offset,
length]}}, written last (station-history reads a tile's file through it; the old one-file-a-tile runs still read).

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
# A pack's most (station-history's upload reads a body whole, 95 MB at most, in a Worker's 128 MB).
PACK_BYTES = 48_000_000
# Tiles made at a time before their files go into the pack (bounds the memory: ~3 MB a tile file at most).
CHUNK = 32
# The zoom a tap reads (station-history SERIES_ZOOMS, the same): the 1 km models and the Mix at 7,
# the 2-2.5 km ones at 6, the others their finest.
ZOOMS = {"mix": 7, "metnordic-1km": 7, "arome": 7, "icon-ch1": 7, "icon-d2": 6, "dini": 6, "harm-nl": 6, "icon-2i": 6,
         "harm-fi": 6, "aladin": 6, "ukmo": 6, "icon-ch2": 6, "arome25": 6}
# The regional models' weather a zoom under their finest (about 2.5 km: enough for air and rain;
# station-history's layerAt reads the finest or the one under it).
ZOOMS.update({f"{folder}-{layer}": 5 for folder in ("dini-wx", "harm-nl-wx", "icon-d2-wx", "arome-wx", "icon-ch1-wx", "icon-2i-wx")
              for layer in ("temp", "rain")})
SETS = ["mix", "mix-world", "metnordic-1km", "dini", "arome", "ukmo", "icon-ch2", "arome25", "knmi-eu", "harm-nl", "icon-d2", "icon-ch1", "icon-2i", "harm-fi", "aladin",
        "icon-eu", "ecmwf", "gfs", "icon", "gdps",
        # The weather's and the waves' too (the owner, 18.10.2026: a place's weather and waves from our
        # files, any place): "<folder>:<layer>", named "<folder>-<layer>" (the waves their own sets).
        "mix-wx:temp", "mix-wx:rain", "mix-world-wx:temp", "mix-world-wx:rain",
        "ecmwf:temp", "ecmwf:rain", "ecmwf:cloud", "ecmwf:sky", "icon-eu-wx:temp", "icon-eu-wx:rain", "icon-eu-wx:cloud",
        "metnordic-1km-wx:temp", "metnordic-1km-wx:rain", "gfs:temp", "gfs:rain", "icon:temp", "icon:rain",
        "gdps:temp", "gdps:rain", "ewam", "ecmwf-waves",
        # The regional models' air and rain too (the owner, 19.10.2026: riders' own spots and taps with
        # every model's weather, as the catalog spots), at zoom 5 (WEATHER_ZOOM).
        "dini-wx:temp", "dini-wx:rain", "harm-nl-wx:temp", "harm-nl-wx:rain", "icon-d2-wx:temp", "icon-d2-wx:rain",
        "arome-wx:temp", "arome-wx:rain", "icon-ch1-wx:temp", "icon-ch1-wx:rain", "icon-2i-wx:temp", "icon-2i-wx:rain",
        # The sea's level and temperature (station-history /sea; the owner, 18.10.2026: off Open-Meteo).
        "sea", "sea-world"]


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
        return key, series_file(times, size, pixels)

    packs, at, buffer, total = [], {}, bytearray(), 0

    def send():
        """The pack so far out (local or uploaded), the next one started."""
        nonlocal buffer
        if not buffer:
            return
        name = f"v3/pack-{len(packs)}.bin"
        path = f"series/{set_name}/{run}/{name}"
        if local:
            (local / path).parent.mkdir(parents=True, exist_ok=True)
            (local / path).write_bytes(bytes(buffer))
        else:
            world_tiles.upload(path, bytes(buffer))
        packs.append(name)
        buffer = bytearray()

    # Four tiles at a time (each every hour's tile, about 40 MB while it's made), CHUNK at a time into the packs.
    with ThreadPoolExecutor(4) as pool:
        for c in range(0, len(tiles), CHUNK):
            for key, data in pool.map(one, tiles[c:c + CHUNK]):
                if buffer and len(buffer) + len(data) > PACK_BYTES:
                    send()
                at[f"{key[0]}/{key[1]}/{key[2]}"] = [len(packs), len(buffer), len(data)]
                buffer += data
                total += len(data)
    send()
    done = json.dumps({"tiles": len(tiles), "hours": len(frames), "bytes": total, "packs": packs, "at": at},
                      separators=(",", ":")).encode()
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
    print(f"{set_name} {run}: {len(tiles)} series in {len(packs)} packs, {total / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)


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
    # Out without the libraries' clean-up: their exit can crash Python after the work is published
    # ("double free or corruption", 18.10.2026), failing the run and the workflow's next step.
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
