"""An area for SWAN's tables (the owner, 19.11.2026: "Yes, build it"; WindguruStationWatch docs/backlog/design.md "The
waves engine"): the sea bed and the land over a box round a spot, made from our own files the way the web map's coarse
fronts grid makes them (WindguruStationWatch tools/webmap/smooth.js FRAGMENT_REFRACT's depthAt with uBig, and
breakwaterLand), so SWAN sees the coast and walls the map draws:
  - the coast (tiles/coast, v5: 127.5 on the shore, 16 levels a pixel, land up; squares all land listed, all sea none):
    the distance to the shore at each cell's middle, read between the zoom-10 pixels; unknown as open sea;
  - the depth (tiles/depth: 1 + sqrt(depth / 200 m) x 254, 0 land or unknown; the finest zoom a square's pack has):
    near the shore (200-800 m) blended with a beach (0.2 + 0.12 d^(2/3)), where unknown the beach alone;
  - land where the coast says so, the bed under 0.1 m, or a breakwater or groyne (tiles/breakwaters) within 3/4 of a
    cell of the cell's middle.
The tide is 0 (the Baltic's; a big tide's levels later).

  python3 swan/area.py LAT LON [SIDE_M] [CELLS]   prints the grid's sums (a check), e.g. 56.5505 20.986
"""
import io
import json
import math
import struct
import sys
import urllib.request
from functools import lru_cache

import numpy as np
from PIL import Image

FILES = "https://files.meteogust.com/tiles/"
UA = {"User-Agent": "meteogust-swan/1"}
EARTH = 40075016.686


def _get(url, rng=None):
    h = dict(UA)
    if rng:
        h["Range"] = f"bytes={rng[0]}-{rng[0] + rng[1] - 1}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 3:
                raise
        except OSError:
            if attempt == 3:
                raise


@lru_cache(None)
def _index(kind):
    ix = json.loads(_get(FILES + kind + "/index.json"))
    ix["_folder"] = FILES + kind + "/" + (ix["folder"] + "/" if ix.get("folder") else "")
    ix["_packs"], ix["_land"] = set(ix.get("packs", [])), set(ix.get("land", []))
    return ix


@lru_cache(None)
def _header(url):
    head = _get(url, (0, 8))
    if not head or head[:4] != b"MGT1":
        return None
    n = struct.unpack("<I", head[4:8])[0]
    h = json.loads(_get(url, (8, n)))
    h["_start"] = 8 + n
    return h


@lru_cache(4096)
def _tile(kind, z, x, y):
    """A tile's grey (uint8, 256 x 256), or "land" / None (unknown: no pack, no tile)."""
    ix = _index(kind)
    sq = f"{x >> (z - 5)}-{y >> (z - 5)}"
    if sq in ix["_land"]:
        return "land"
    if sq not in ix["_packs"]:
        return "land" if kind == "depth" else None
    url = ix["_folder"] + f"5-{sq}.pack"
    h = _header(url)
    if not h or f"{z}/{x}/{y}" not in h["tiles"]:
        return None
    off, ln = h["tiles"][f"{z}/{x}/{y}"]
    im = Image.open(io.BytesIO(_get(url, (h["_start"] + off, ln))))
    return np.asarray(im.convert("L"))


def _finest(kind, mx, my):
    """The finest zoom (<= 10) the pack under a place has."""
    ix = _index(kind)
    x, y = int(mx * 32), int(my * 32)
    sq = f"{x}-{y}"
    if sq not in ix["_packs"]:
        return 10
    h = _header(ix["_folder"] + f"5-{sq}.pack")
    return max(int(z) for z in h["sizes"]) if h else 10


def _sample(kind, z, mx, my):
    """The grey between pixels at a place (world 0-1), and whether it's known: (value, known); land squares 255."""
    n = 2 ** z * 256
    fx, fy = mx * n - 0.5, my * n - 0.5
    x0, y0 = math.floor(fx), math.floor(fy)
    tx, ty = fx - x0, fy - y0
    total = wsum = 0.0
    for dx, dy, w in ((0, 0, (1 - tx) * (1 - ty)), (1, 0, tx * (1 - ty)), (0, 1, (1 - tx) * ty), (1, 1, tx * ty)):
        px, py = x0 + dx, y0 + dy
        t = _tile(kind, z, px >> 8, py >> 8)
        if t is None:
            continue
        v = 255.0 if (isinstance(t, str) and kind == "coast") else 0.0 if isinstance(t, str) else float(t[py & 255, px & 255])
        total += w * v
        wsum += w
    return (total / wsum, True) if wsum > 0.5 else (0.0, False)


def build(box, W, H):
    """The depth (m, H x W, row 0 the box's north edge; -1 land) over box (world 0-1: x, y, width, height)."""
    lat = math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2))))
    coast_m = EARTH * math.cos(lat) / (256 * 2 ** 10)
    zd = _finest("depth", box[0] + box[2] / 2, box[1] + box[3] / 2)
    depth = np.zeros((H, W), np.float32)
    for r in range(H):
        my = box[1] + (r + 0.5) / H * box[3]
        for c in range(W):
            mx = box[0] + (c + 0.5) / W * box[2]
            g, known = _sample("coast", 10, mx, my)
            e = (127.5 - g) / 16 * coast_m if known else 5000.0
            if e < 0:
                depth[r, c] = -1
                continue
            d = max(e, 0.0)
            dean = min(0.2 + 0.12 * min(d, 20000.0) ** (2 / 3), 200.0)
            t, tknown = _sample("depth", zd, mx, my)
            if tknown and t > 0.5:
                v = (t - 1) / 254
                tile = 200 * v * v
                s = min(max((d - 200) / 600, 0), 1)
                s = s * s * (3 - 2 * s)
                h = min(dean, tile + 2) * (1 - s) + tile * s
            else:
                h = dean
            depth[r, c] = h if h >= 0.1 else -1
    _breakwaters(depth, box, W, H)
    return depth


def _breakwaters(depth, box, W, H):
    """Breakwaters and groynes as land: a cell within 3/4 of a cell of a line or an outline (smooth.js breakwaterLand)."""
    ix = json.loads(_get(FILES + "breakwaters/v1/index.json") or b"{}")
    if not ix:
        return
    z, have = ix["zoom"], set(ix["tiles"])
    n = 2 ** z
    keys = {f"{x}-{y}" for x in range(int(box[0] * n), int((box[0] + box[2]) * n) + 1)
            for y in range(int(box[1] * n), int((box[1] + box[3]) * n) + 1)} & have
    to_cell = lambda p: (((p[0] + 180) / 360 - box[0]) / box[2] * W - 0.5,  # noqa: E731
                         ((1 - math.log(math.tan(math.pi / 4 + math.radians(p[1]) / 2)) / math.pi) / 2 - box[1]) / box[3] * H - 0.5)

    def mark(a, b):
        (x0, y0), (x1, y1) = to_cell(a), to_cell(b)
        if max(x0, x1) < -1 or max(y0, y1) < -1 or min(x0, x1) > W or min(y0, y1) > H:
            return
        steps = math.ceil(math.hypot(x1 - x0, y1 - y0) * 4) + 1
        for k in range(steps + 1):
            x, y = x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps
            for cy in range(math.ceil(y - 0.75), math.floor(y + 0.75) + 1):
                for cx in range(math.ceil(x - 0.75), math.floor(x + 0.75) + 1):
                    if 0 <= cx < W and 0 <= cy < H and (cx - x) ** 2 + (cy - y) ** 2 <= 0.5625:
                        depth[cy, cx] = -1

    for key in keys:
        t = json.loads(_get(f"{FILES}breakwaters/v1/{z}-{key}.json") or b'{"p":[],"l":[]}')
        for rings in t.get("p", []):
            for ring in rings:
                for i in range(1, len(ring)):
                    mark(ring[i - 1], ring[i])
        for _, line in t.get("l", []):
            for i in range(1, len(line)):
                mark(line[i - 1], line[i])


def box_round(lat, lon, side_m=8700.0):
    """A square box (on the ground) of side_m round a place, world 0-1: [x, y, width, height]."""
    w = side_m / (EARTH * math.cos(math.radians(lat)))
    mx = (lon + 180) / 360
    my = (1 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi) / 2
    return [mx - w / 2, my - w / 2, w, w]


if __name__ == "__main__":
    lat, lon = float(sys.argv[1]), float(sys.argv[2])
    side = float(sys.argv[3]) if len(sys.argv) > 3 else 8700.0
    cells = int(sys.argv[4]) if len(sys.argv) > 4 else 256
    d = build(box_round(lat, lon, side), cells, cells)
    sea = d >= 0
    print(f"{cells}x{cells} cells of {side / cells:.0f} m: sea {sea.mean() * 100:.0f} %, depth max {d[sea].max():.1f} m, mean {d[sea].mean():.1f} m")
