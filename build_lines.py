"""The map lines the app draws over its map colors, like Windy's (run once, by hand, like
build_coast.py): the coastline, lakes, rivers and country borders of the regional maps' box,
simplified, in one small binary file the app carries (MapLines.bin).

- Coastline: the 0.5 line of the fine land mask (coast.png, OpenStreetMap land polygons), so it
  runs exactly where the map colors change from sea to land.
- Lakes (named natural=water, not river areas: ponds are rarely named; from about 0.05 km2), rivers
  (waterway=river) and country borders on land (admin_level 2, not maritime): OpenStreetMap,
  read through the Overpass API in 1 degree tiles (cached in lines-cache/, about 250 MB).
  © OpenStreetMap contributors, ODbL.

Each line has a detail level, so the app draws less when zoomed out: 0 always (long coasts,
lakes from 5 km2, rivers 100 km and longer, borders), 1 from the middle zoom (lakes from 0.5 km2,
rivers from 25 km, coasts from 5 km), 2 close up (the rest).

    python build_lines.py              writes map-lines.bin
    python build_lines.py --fetch      reads the missing tiles from Overpass first

File (little-endian): "SWL1", south, north, west, east (float64: the box the points are
quantized over, Web Mercator), count (uint32), then per line: kind (uint8: 0 coast, 1 lake,
2 river, 3 border), detail (uint8), flags (uint8: 1 closed, 2 filled), 0, n (uint32), n x
(x, y) uint16 (0 = west / north, 65535 = east / south).
"""
import json
import math
import struct
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from skimage import measure

import metnordic_map

HERE = Path(__file__).parent
CACHE = HERE / "lines-cache"
# Public Overpass servers, taken in turn when one is busy.
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
USER_AGENT = "baltic-aladin map lines (one-time build; https://github.com/AndreySemjonov/baltic-aladin)"
SOUTH, NORTH, WEST, EAST = metnordic_map.SOUTH, metnordic_map.NORTH, metnordic_map.WEST, metnordic_map.EAST
# Lines are kept a little past the box, so they don't stop at its edge.
MARGIN = 0.3
BOX = (SOUTH - MARGIN, NORTH + MARGIN, WEST - MARGIN, EAST + MARGIN)
COAST, LAKE, RIVER, BORDER = 0, 1, 2, 3
# How far a simplified line may stray, m.
TOLERANCE = {COAST: 60, LAKE: 25, RIVER: 30, BORDER: 50}


def merc(lat):
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def query(south, west, north, east):
    box = f"({south},{west},{north},{east})"
    return ("[out:json][timeout:180];("
            f'way["natural"="water"]["name"]["water"!~"^(river|canal|stream|ditch)$"]{box};'
            f'relation["natural"="water"]["name"]["water"!~"^(river|canal|stream|ditch)$"]{box};'
            f'way["waterway"="river"]{box};'
            f'way["boundary"="administrative"]["admin_level"="2"]["maritime"!="yes"]{box};'
            ");out geom qt;")


def land_tiles():
    """1 degree tiles of the box with any land in the coast mask."""
    coast = np.asarray(Image.open(HERE / "coast.png").convert("L"))
    h, w = coast.shape
    top, bottom = merc(NORTH), merc(SOUTH)
    tiles = []
    for lat in range(math.floor(SOUTH), math.ceil(NORTH)):
        for lon in range(math.floor(WEST), math.ceil(EAST)):
            s, n, wst, e = max(lat, SOUTH), min(lat + 1, NORTH), max(lon, WEST), min(lon + 1, EAST)
            if s >= n or wst >= e:
                continue
            r0, r1 = int((top - merc(n)) / (top - bottom) * h), int((top - merc(s)) / (top - bottom) * h)
            c0, c1 = int((wst - WEST) / (EAST - WEST) * w), int((e - WEST) / (EAST - WEST) * w)
            if coast[r0:max(r1, r0 + 1), c0:max(c1, c0 + 1)].max() > 0:
                tiles.append((s, wst, n, e))
    return tiles


def fetch():
    CACHE.mkdir(exist_ok=True)
    tiles = land_tiles()
    for k, (s, w, n, e) in enumerate(tiles):
        path = CACHE / f"{s:.1f}_{w:.1f}.json"
        if path.exists():
            continue
        for attempt in range(9):
            server = OVERPASS[attempt % len(OVERPASS)]
            try:
                data = urllib.parse.urlencode({"data": query(s, w, n, e)}).encode()
                request = urllib.request.Request(server, data=data, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=300) as reply:
                    body = reply.read()
                json.loads(body)
                path.write_bytes(body)
                print(f"tile {k + 1}/{len(tiles)} {s},{w}: {len(body) / 1e6:.1f} MB")
                break
            except Exception as error:  # busy (429/504): the next server, waiting a little more each round
                print(f"tile {s},{w} ({server.split('/')[2]}): {error}")
                time.sleep(10 * (attempt // len(OVERPASS) + 1))
        time.sleep(3)


def metres(points):
    """lat/lon points -> local metres (equirectangular around their middle)."""
    lat = np.array([p[0] for p in points]); lon = np.array([p[1] for p in points])
    k = math.cos(math.radians(lat.mean()))
    return np.stack([lon * 111320 * k, lat * 110540], axis=1)


def simplify(points, tolerance):
    """Douglas-Peucker on lat/lon points, tolerance in metres."""
    if len(points) < 3:
        return points
    xy = metres(points)
    keep = np.zeros(len(points), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = xy[a], xy[b]
        d = q - p
        length = math.hypot(*d)
        seg = xy[a + 1:b] - p
        if length == 0:
            dist = np.hypot(seg[:, 0], seg[:, 1])
        else:
            dist = np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / length
        i = int(np.argmax(dist))
        if dist[i] > tolerance:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return [p for p, k in zip(points, keep) if k]


def length_km(points):
    xy = metres(points)
    return float(np.hypot(*np.diff(xy, axis=0).T).sum()) / 1000 if len(points) > 1 else 0


def area_km2(points):
    xy = metres(points)
    x, y = xy[:, 0], xy[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2 / 1e6


def inside_runs(points):
    """The parts of a line inside BOX (a line leaving it is cut there)."""
    s, n, w, e = BOX
    runs, run = [], []
    for p in points:
        if s <= p[0] <= n and w <= p[1] <= e:
            run.append(p)
        else:
            if len(run) > 1:
                runs.append(run)
            run = []
    if len(run) > 1:
        runs.append(run)
    return runs


def stitch(ways):
    """Ways (point lists) joined end to end into rings; what can't close stays open."""
    key = lambda p: (round(p[0], 7), round(p[1], 7))
    ways = [list(w) for w in ways if len(w) > 1]
    rings, open_lines = [], []
    while ways:
        ring = ways.pop()
        changed = True
        while key(ring[0]) != key(ring[-1]) and changed:
            changed = False
            for i, w in enumerate(ways):
                if key(w[0]) == key(ring[-1]):
                    ring += w[1:]
                elif key(w[-1]) == key(ring[-1]):
                    ring += w[-2::-1]
                elif key(w[-1]) == key(ring[0]):
                    ring = w[:-1] + ring
                elif key(w[0]) == key(ring[0]):
                    ring = w[:0:-1] + ring
                else:
                    continue
                ways.pop(i)
                changed = True
                break
        (rings if key(ring[0]) == key(ring[-1]) and len(ring) > 3 else open_lines).append(ring)
    return rings, open_lines


def osm_lines():
    ways, relations = {}, {}
    for path in sorted(CACHE.glob("*.json")):
        for element in json.loads(path.read_text(encoding="utf-8"))["elements"]:
            (ways if element["type"] == "way" else relations)[element["id"]] = element
    lines = []
    # Lakes: closed ways and relations' rings.
    for way in ways.values():
        tags = way.get("tags", {})
        if tags.get("natural") != "water" or "geometry" not in way:
            continue
        points = [(p["lat"], p["lon"]) for p in way["geometry"]]
        closed = len(points) > 3 and points[0] == points[-1]
        area = area_km2(points) if closed else 0
        if closed and area >= 0.05:
            lines.append((LAKE, 0 if area >= 5 else 1 if area >= 0.5 else 2, 3, points))
    for relation in relations.values():
        members = relation.get("members", [])
        outer = [[(p["lat"], p["lon"]) for p in m["geometry"]] for m in members if m.get("role") == "outer" and "geometry" in m]
        inner = [[(p["lat"], p["lon"]) for p in m["geometry"]] for m in members if m.get("role") == "inner" and "geometry" in m]
        rings, loose = stitch(outer)
        area = sum(area_km2(r) for r in rings)
        if area < 0.05:
            continue
        detail = 0 if area >= 5 else 1 if area >= 0.5 else 2
        lines += [(LAKE, detail, 3, r) for r in rings] + [(LAKE, detail, 0, l) for l in loose]
        islands, loose_inner = stitch(inner)
        lines += [(LAKE, 2 if area_km2(r) < 0.5 else detail, 1, r) for r in islands]
        lines += [(LAKE, detail, 0, l) for l in loose_inner]
    # Rivers: their detail by the whole river's length: the ways of the same name that join end
    # to end (names like Mustjõgi repeat across the country, so the name alone isn't one river).
    rivers = [w for w in ways.values() if w.get("tags", {}).get("waterway") == "river" and "geometry" in w]
    parent = list(range(len(rivers)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ends = defaultdict(list)
    for i, w in enumerate(rivers):
        for p in (w["geometry"][0], w["geometry"][-1]):
            ends[(w["tags"].get("name", ""), round(p["lat"], 6), round(p["lon"], 6))].append(i)
    for (name, _, _), group in ends.items():
        if name:
            for i in group[1:]:
                parent[root(i)] = root(group[0])
    lengths = [length_km([(p["lat"], p["lon"]) for p in w["geometry"]]) for w in rivers]
    total = defaultdict(float)
    for i, km in enumerate(lengths):
        total[root(i)] += km
    for i, w in enumerate(rivers):
        km = total[root(i)] if w["tags"].get("name") else lengths[i]
        detail = 0 if km >= 100 else 1 if km >= 25 else 2
        lines.append((RIVER, detail, 0, [(p["lat"], p["lon"]) for p in w["geometry"]]))
    # Borders on land.
    for w in ways.values():
        tags = w.get("tags", {})
        if tags.get("boundary") == "administrative" and tags.get("maritime") != "yes" and "geometry" in w:
            lines.append((BORDER, 0, 0, [(p["lat"], p["lon"]) for p in w["geometry"]]))
    return lines


def coast_lines():
    """The 0.5 line of the fine land mask, with its detail by length."""
    coast = np.asarray(Image.open(HERE / "coast.png").convert("L")).astype(np.float32) / 255
    h, w = coast.shape
    top, bottom = merc(NORTH), merc(SOUTH)
    lines = []
    for contour in measure.find_contours(coast, 0.5):
        rows, cols = contour[:, 0], contour[:, 1]
        lon = WEST + (cols + 0.5) / w * (EAST - WEST)
        y = top - (rows + 0.5) / h * (top - bottom)
        lat = np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)
        points = list(zip(lat.tolist(), lon.tolist()))
        km = length_km(points)
        if km < 0.8:
            continue
        closed = points[0] == points[-1]
        lines.append((COAST, 0 if km >= 30 else 1 if km >= 5 else 2, 1 if closed else 0, points))
    return lines


def main():
    if "--fetch" in sys.argv:
        fetch()
    lines = coast_lines() + osm_lines()
    s, n, w, e = BOX
    top, bottom = merc(n), merc(s)
    out = bytearray(b"SWL1")
    out += struct.pack("<4d", s, n, w, e)
    records, points_total, counts = [], 0, defaultdict(int)
    for kind, detail, flags, points in lines:
        runs = [points] if all(s <= p[0] <= n and w <= p[1] <= e for p in points) else inside_runs(points)
        if len(runs) != 1 or runs[0] is not points:
            flags = 0  # cut at the box: no longer closed
        for run in runs:
            run = simplify(run, TOLERANCE[kind])
            if len(run) < 2:
                continue
            xs = np.clip(np.rint((np.array([p[1] for p in run]) - w) / (e - w) * 65535), 0, 65535).astype("<u2")
            ys = np.clip(np.rint((top - np.array([merc(p[0]) for p in run])) / (top - bottom) * 65535), 0, 65535).astype("<u2")
            record = struct.pack("<BBBBI", kind, detail, flags, 0, len(run)) + np.stack([xs, ys], axis=1).tobytes()
            records.append(record)
            points_total += len(run)
            counts[(kind, detail)] += 1
    out += struct.pack("<I", len(records))
    for record in records:
        out += record
    (HERE / "map-lines.bin").write_bytes(bytes(out))
    names = {COAST: "coast", LAKE: "lake", RIVER: "river", BORDER: "border"}
    print({f"{names[k]}{d}": c for (k, d), c in sorted(counts.items())})
    print(f"map-lines.bin: {len(records)} lines, {points_total} points, {len(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
