"""Breakwaters and groynes for the web map's waves (the owner, 19.11.2026, Liepaja's satellite photo next to our map:
"In liepaja we missing big part of mall and its brakes a lot of waves there too"). Our base map (OpenFreeMap, the
OpenMapTiles schema) keeps piers but leaves man_made=breakwater out: Liepaja's detached outer breakwater, 2.5 km in the
sea, wasn't there, and the waves ran through it. The map reads these as land (the waves' lee, their fronts, the surf)
and draws them.

OpenStreetMap through the Overpass API (as build_lines.py), 5 degree cells over Europe's and North Africa's coasts
(breakwaters are few: a cell is one query), cached in breakwaters-cache/. © OpenStreetMap contributors, ODbL.

    tiles/breakwaters/v1/8-x-y.json   the ones in that zoom-8 tile: {"p": [polygon rings ([[lon, lat], ...] outer first)],
                                      "l": [[width m, [[lon, lat], ...]], ...]} (lines: a breakwater 10 m wide, a groyne 4 m)
    tiles/breakwaters/v1/index.json   {"tiles": ["x-y", ...], "zoom": 8, "source": ..., "made": ...}

    python build_breakwaters.py [--local DIR] [--box south,west,north,east]
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
# Europe's and North Africa's coasts (Tarifa, Morocco, the Canaries' north).
BOX = (27, -25, 72, 45)
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


def fetch(cells):
    CACHE.mkdir(exist_ok=True)
    for k, (s, w, n, e) in enumerate(cells):
        path = CACHE / f"{s}_{w}.json"
        if path.exists():
            continue
        for attempt in range(9):
            server = OVERPASS[attempt % len(OVERPASS)]
            try:
                data = urllib.parse.urlencode({"data": query(s, w, n, e)}).encode()
                request = urllib.request.Request(server, data=data, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=400) as reply:
                    body = reply.read()
                json.loads(body)
                path.write_bytes(body)
                print(f"cell {k + 1}/{len(cells)} {s},{w}: {len(body) / 1e6:.2f} MB", flush=True)
                break
            except Exception as error:  # busy (429/504): the next server, waiting a little more each round
                print(f"cell {s},{w} ({server.split('/')[2]}): {error}", flush=True)
                time.sleep(15 * (attempt // len(OVERPASS) + 1))
        else:
            raise SystemExit(f"cell {s},{w}: no answer")
        time.sleep(3)


def tile_of(lon, lat):
    n = 2 ** ZOOM
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def shapes(cells):
    """Each breakwater once (cells overlap on their edges): ("p", rings) or ("l", width, points)."""
    seen = set()
    for s, w, n, e in cells:
        for el in json.loads((CACHE / f"{s}_{w}.json").read_text())["elements"]:
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
    box = tuple(float(v) for v in args[args.index("--box") + 1].split(",")) if "--box" in args else BOX
    s0, w0, n0, e0 = box
    cells = [(s, w, min(s + CELL, n0), min(w + CELL, e0))
             for s in range(math.floor(s0), math.ceil(n0), CELL) for w in range(math.floor(w0), math.ceil(e0), CELL)]
    fetch(cells)
    tiles = defaultdict(lambda: {"p": [], "l": []})
    count = 0
    for shape in shapes(cells):
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
