"""OPERA's radar composite as GeoTIFF where EUMETNET publishes only its HDF5 (the owner, 26.10.2026: "lets make B").

EUMETNET's open bucket (openradar-24h, CloudFerro) had each composite twice, ODIM HDF5 and GeoTIFF, until 26.10.2026
00:20 UTC; since 00:25 the HDF5 only. station-history reads the GeoTIFF's tiles by byte range (the radar map, the rain
alerts: operaImages, operaImage, operaTile), so here each reflectivity image (DBZH, 1 km, every 5 minutes) without
EUMETNET's GeoTIFF is written as one laid out like theirs and uploaded to files.meteogust.com/opera/<its key>.tiff; the
worker reads ours where theirs is missing. The same values: their GeoTIFF was the HDF5's data and quality layers as
float32, two samples a pixel, 512 x 512 tiles, deflate, no predictor, "undetect" (no echo) as NaN, no radar -9999000
(compared cell by cell on 00:20 UTC's pair). Standard library and numpy and h5py (the runner's image) only.

    python opera_tiff.py [--local DIR] [--minutes 40]
"""
import io
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

BUCKET = "https://s3.waw3-1.cloudferro.com/openradar-24h/"
OURS = "https://files.meteogust.com/opera/"
UPLOAD = "https://data.meteogust.com/tiles/upload/opera/"
AGENT = {"User-Agent": "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"}
KINDS = ("DBZH",)
NODATA, UNDETECT = -9999000.0, -8888000.0
# Their GeoTIFF's grid (DBZH 1 km: 3800 x 4400 cells): its pixel size and tie point, as theirs had them.
GRID = {"DBZH": (3800, 4400, 1000.0, (0.0, 0.0, 0.0, -500.0002714332659, 499.9999123872258, 0.0))}
TILE = 512


def get(url, head=False, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=AGENT, method="HEAD" if head else "GET")
            with urllib.request.urlopen(request, timeout=60) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code == 404 or attempt == tries - 1:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == tries - 1:
                raise
        time.sleep(5 * (attempt + 1))


def keys(minutes):
    """The composites' keys of the last `minutes` (today's folder and, past midnight, yesterday's)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=minutes)
    out = []
    for day in sorted({since.date(), now.date()}):
        prefix = f"{day:%Y/%m/%d}/OPERA/COMP/"
        after = f"{prefix}OPERA@{since:%Y%m%dT%H%M}"
        token = None
        while True:
            url = f"{BUCKET}?list-type=2&prefix={prefix}&max-keys=1000&start-after={after}" + (
                f"&continuation-token={urllib.request.quote(token)}" if token else "")
            xml = get(url).decode()
            out += re.findall(r"<Key>([^<]+)</Key>", xml)
            token = (re.findall(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml) or [None])[0]
            if "<IsTruncated>true</IsTruncated>" not in xml or not token:
                break
    return out


def tiff(values, quality, pixel, tie):
    """A little-endian tiled GeoTIFF of two float32 samples a pixel (value, quality), deflate, the directory and the
    tiles' offsets first (station-history reads the first 16 KB, then the tiles it needs)."""
    height, width = values.shape
    across, down = -(-width // TILE), -(-height // TILE)
    tiles = []
    for ty in range(down):
        for tx in range(across):
            block = np.full((TILE, TILE, 2), NODATA, dtype="<f4")
            part_v = values[ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE]
            part_q = quality[ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE]
            block[:part_v.shape[0], :part_v.shape[1], 0] = part_v
            block[:part_q.shape[0], :part_q.shape[1], 1] = part_q
            tiles.append(zlib.compress(block.tobytes(), 6))
    n = len(tiles)
    nodata = b"-9999000\x00"
    # (tag, type, count, value bytes): 3 SHORT, 4 LONG, 12 DOUBLE, 2 ASCII.
    entries = [(256, 4, 1, struct.pack("<I", width)), (257, 4, 1, struct.pack("<I", height)),
               (258, 3, 2, struct.pack("<HH", 32, 32)), (259, 3, 1, struct.pack("<H", 8)),
               (262, 3, 1, struct.pack("<H", 1)), (277, 3, 1, struct.pack("<H", 2)),
               (284, 3, 1, struct.pack("<H", 1)), (317, 3, 1, struct.pack("<H", 1)),
               (322, 4, 1, struct.pack("<I", TILE)), (323, 4, 1, struct.pack("<I", TILE)),
               (324, 4, n, None), (325, 4, n, struct.pack(f"<{n}I", *[len(t) for t in tiles])),
               (338, 3, 1, struct.pack("<H", 0)), (339, 3, 2, struct.pack("<HH", 3, 3)),
               (33550, 12, 3, struct.pack("<3d", pixel, pixel, 0.0)), (33922, 12, 6, struct.pack("<6d", *tie)),
               (42113, 2, len(nodata), nodata)]
    ifd_size = 2 + 12 * len(entries) + 4
    extra_at = 8 + ifd_size
    # The values over 4 bytes go after the directory; then the tiles.
    sizes = [len(v) if v is not None else 4 * n for _, _, _, v in entries]
    extra_total = sum(s + (s % 2) for s in sizes if s > 4)
    first_tile = extra_at + extra_total
    offsets, at = [], first_tile
    for t in tiles:
        offsets.append(at)
        at += len(t)
    out = bytearray(b"II" + struct.pack("<HI", 42, 8))
    out += struct.pack("<H", len(entries))
    extra, cursor = bytearray(), extra_at
    for (tag, kind, count, value), size in zip(entries, sizes):
        if value is None:
            value = struct.pack(f"<{n}I", *offsets)
        if size <= 4:
            out += struct.pack("<HHI", tag, kind, count) + value.ljust(4, b"\x00")
        else:
            out += struct.pack("<HHII", tag, kind, count, cursor)
            extra += value + (b"\x00" if size % 2 else b"")
            cursor += size + (size % 2)
    out += struct.pack("<I", 0)
    out += extra
    assert len(out) == first_tile, (len(out), first_tile)
    if first_tile > 16384:
        raise SystemExit(f"the directory ends at {first_tile}, past the 16 KB the worker reads")
    for t in tiles:
        out += t
    return bytes(out)


def convert(data, kind):
    import h5py  # the runner's image has it (not every GitHub job's)
    with h5py.File(io.BytesIO(data), "r") as f:
        where = f["where"].attrs
        width, height, pixel, tie = GRID[kind]
        if (int(where["xsize"]), int(where["ysize"]), float(where["xscale"])) != (width, height, pixel):
            raise RuntimeError(f"{kind}: grid {where['xsize']}x{where['ysize']} at {where['xscale']} m, not {width}x{height} at {pixel}")
        what = f["dataset1/data1/what"].attrs
        raw = f["dataset1/data1/data"][:]
        quality = f["dataset1/data1/quality1/data"][:].astype("<f4")
    gain, offset = float(what.get("gain", 1.0)), float(what.get("offset", 0.0))
    nodata, undetect = float(what.get("nodata", NODATA)), float(what.get("undetect", UNDETECT))
    values = (raw * gain + offset).astype("<f4")
    values[raw == undetect] = np.nan
    values[raw == nodata] = NODATA
    return tiff(values, quality, pixel, tie)


def main(args):
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    minutes = int(args[args.index("--minutes") + 1]) if "--minutes" in args else 40
    token = os.environ.get("INGEST_TOKEN")
    if not local and not token:
        raise SystemExit("INGEST_TOKEN is not set")
    listed = keys(minutes)
    have = set(listed)
    made = skipped = 0
    for key in listed:
        m = re.search(r"@(\w+)\.h5$", key)
        if not m or m.group(1) not in KINDS:
            continue
        name = key[:-3] + ".tiff"
        if name in have:
            skipped += 1  # EUMETNET's own
            continue
        if local and (local / "opera" / name).exists():
            continue
        if not local:
            try:
                get(OURS + name, head=True)
                continue  # ours, made before
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
        started = time.time()
        body = convert(get(BUCKET + key), m.group(1))
        if local:
            path = local / "opera" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        else:
            request = urllib.request.Request(UPLOAD + name, data=body, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "Content-Type": "image/tiff", **AGENT})
            with urllib.request.urlopen(request, timeout=120) as reply:
                reply.read()
        made += 1
        print(f"{name.split('/')[-1]}: {len(body) / 1e6:.1f} MB in {time.time() - started:.0f} s", flush=True)
    print(f"made {made}, EUMETNET's own {skipped}, of {len(listed)} files in the last {minutes} min", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
