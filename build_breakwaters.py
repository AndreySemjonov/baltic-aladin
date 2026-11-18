"""Breakwaters and groynes for the web map's waves (the owner, 19.11.2026, Liepaja's satellite photo next to our map:
"In liepaja we missing big part of mall and its brakes a lot of waves there too"). Our base map (OpenFreeMap, the
OpenMapTiles schema) keeps piers but leaves man_made=breakwater out: Liepaja's detached outer breakwater, 2.5 km in the
sea, wasn't there, and the waves ran through it. The map reads these as land (the waves' lee, their fronts, the surf)
and draws them.

OpenStreetMap through the Overpass API (as build_lines.py), the world's (the owner, 19.11.2026: "we have to add it
worldwide ... So we not have same issue in future"): one query a zoom-5 square with a coast (our coast tiles' index:
sea and lake shores), split in four where it's too much for one, cached in breakwaters-cache/. © OpenStreetMap
contributors, ODbL.

    tiles/breakwaters/v1/8-x-y.json   the ones in that zoom-8 tile: {"p": [polygon rings ([[lon, lat], ...] outer first)],
                                      "l": [[width m, [[lon, lat], ...]], ...]} (lines: a breakwater 10 m wide, a groyne 4 m)
    tiles/breakwaters/v1/index.json   {"tiles": ["x-y", ...], "zoom": 8, "source": ..., "made": ...}

    python build_breakwaters.py [--local DIR] [--box south,west,north,east (5 degree cells; default the world's coasts)]
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "breakwaters-cache"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
USER_AGENT = "MeteoGust breakwaters (one-time build; https://github.com/AndreySemjonov/baltic-aladin)"
COAST_INDEX = "https://files.meteogust.com/tiles/coast/index.json"
CELL = 5
ZOOM = 8
WIDTH = {"breakwater": 10, "groyne": 4}
UPLOAD = "https://data.meteogust.com/tiles/upload/"


def upload(path, data):
    """PUT on our server (station-history /tiles/upload, the INGEST_TOKEN secret), with retries."""
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    for attempt in range(4):
        try:
            request = urllib.request.Request(UPLOAD + path, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=300) as reply:
                return reply.read().decode()
        except (urllib.error.URLError, OSError) as error:
            if (isinstance(error, urllib.error.HTTPError) and error.code < 500) or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def query(south, west, north, east):
    box = f"({south},{west},{north},{east})"
    return ('[out:json][timeout:300][maxsize:536870912];('
            f'way["man_made"~"^(breakwater|groyne)$"]{box};'
            f'relation["man_made"~"^(breakwater|groyne)$"]{box};'
            ");out geom qt;")


def ask(s, w, n, e):
    """One box's answer (bytes), or None when no server could give it whole (busy, or too much: Overpass says so in
    its "remark" with a 200)."""
    for attempt in range(6):
        server = OVERPASS[attempt % len(OVERPASS)]
        try:
            data = urllib.parse.urlencode({"data": query(s, w, n, e)}).encode()
            request = urllib.request.Request(server, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=400) as reply:
                body = reply.read()
            answer = json.loads(body)
            if "error" in answer.get("remark", "") or "timed out" in answer.get("remark", ""):
                print(f"box {s:.2f},{w:.2f}: {answer['remark'][:80]}", flush=True)
                return None
            return body
        except Exception as error:  # busy (429/504): the next server, waiting a little more each round
            print(f"box {s:.2f},{w:.2f} ({server.split('/')[2]}): {error}", flush=True)
            time.sleep(15 * (attempt // len(OVERPASS) + 1))
    return None


def fetch(cells):
    """Each cell's answer cached (split in four, twice at most, where one query can't take it); the cache files."""
    CACHE.mkdir(exist_ok=True)
    files = []

    def get(s, w, n, e, depth, label):
        path = CACHE / f"{s:.4f}_{w:.4f}_{n:.4f}_{e:.4f}.json"
        if path.exists():
            files.append(path)
            return
        body = ask(s, w, n, e)
        time.sleep(3)
        if body is not None:
            path.write_bytes(body)
            files.append(path)
            print(f"{label} {s:.2f},{w:.2f}: {len(body) / 1e6:.2f} MB", flush=True)
            return
        if depth >= 2:
            raise SystemExit(f"box {s},{w},{n},{e}: no answer")
        ms, mw = (s + n) / 2, (w + e) / 2
        for box in ((s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)):
            get(*box, depth + 1, label + "+")

    for k, cell in enumerate(cells):
        get(*cell, 0, f"cell {k + 1}/{len(cells)}")
    return files


def coast_cells():
    """The zoom-5 squares with a coast (our coast tiles' packs: land and water both), as (south, west, north, east)."""
    request = urllib.request.Request(COAST_INDEX, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as reply:
        packs = json.loads(reply.read())["packs"]
    lat = lambda y: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 32))))
    return [(lat(int(y) + 1), int(x) * 11.25 - 180, lat(int(y)), (int(x) + 1) * 11.25 - 180)
            for x, y in (p.split("-") for p in packs)]


def tile_of(lon, lat):
    n = 2 ** ZOOM
    lat = min(max(lat, -85.05), 85.05)
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def shapes(files):
    """Each breakwater once (boxes overlap on their edges): ("p", rings) or ("l", width, points)."""
    seen = set()
    for path in files:
        for el in json.loads(path.read_text())["elements"]:
            key = (el["type"], el["id"])
            if key in seen:
                continue
            seen.add(key)
            kind = el.get("tags", {}).get("man_made")
            width = WIDTH.get(kind, 8)
            if el["type"] == "way":
                pts = [(round(p["lon"], 6), round(p["lat"], 6)) for p in el.get("geometry", [])]
                if len(pts) < 2:
                    continue
                if len(pts) >= 4 and pts[0] == pts[-1] and el.get("tags", {}).get("area") != "no":
                    yield ("p", [pts])
                else:
                    yield ("l", width, pts)
            else:
                outers = [[(round(p["lon"], 6), round(p["lat"], 6)) for p in m.get("geometry", [])]
                          for m in el.get("members", []) if m.get("type") == "way" and m.get("role") in ("outer", "")]
                for ring in outers:
                    if len(ring) >= 4 and ring[0] == ring[-1]:
                        yield ("p", [ring])
                    elif len(ring) >= 2:
                        yield ("l", width, ring)


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    if "--box" in args:
        box = tuple(float(v) for v in args[args.index("--box") + 1].split(","))
        s0, w0, n0, e0 = box
        cells = [(s, w, min(s + CELL, n0), min(w + CELL, e0))
                 for s in range(math.floor(s0), math.ceil(n0), CELL) for w in range(math.floor(w0), math.ceil(e0), CELL)]
    else:
        box, cells = "world", coast_cells()
    print(f"{len(cells)} cells", flush=True)
    files = fetch(cells)
    tiles = defaultdict(lambda: {"p": [], "l": []})
    count = 0
    for shape in shapes(files):
        pts = shape[1][0] if shape[0] == "p" else shape[2]
        # In every zoom-8 tile it touches (by its points' box: a breakwater is short).
        xs, ys = zip(*(tile_of(lon, lat) for lon, lat in pts))
        for x in range(min(xs), max(xs) + 1):
            for y in range(min(ys), max(ys) + 1):
                if shape[0] == "p":
                    tiles[(x, y)]["p"].append(shape[1])
                else:
                    tiles[(x, y)]["l"].append([shape[1], shape[2]])
        count += 1
    print(f"{count} breakwaters and groynes in {len(tiles)} zoom-{ZOOM} tiles", flush=True)

    def put(name, data):
        if local:
            (local / "breakwaters" / "v1").mkdir(parents=True, exist_ok=True)
            (local / "breakwaters" / "v1" / name).write_bytes(data)
        else:
            upload(f"breakwaters/v1/{name}", data)

    total = 0
    for (x, y), shapes_here in sorted(tiles.items()):
        data = json.dumps(shapes_here, separators=(",", ":")).encode()
        total += len(data)
        put(f"{ZOOM}-{x}-{y}.json", data)
    index = {"tiles": [f"{x}-{y}" for x, y in sorted(tiles)], "zoom": ZOOM, "box": box,
             "source": "OpenStreetMap contributors (ODbL): man_made=breakwater, groyne",
             "made": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    put("index.json", json.dumps(index, separators=(",", ":")).encode())
    print(f"{len(tiles)} tiles, {total / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
