"""Breakwaters and groynes for the web map's waves (the owner, 19.11.2026, Liepaja's satellite photo next to our map:
"In liepaja we missing big part of mall and its brakes a lot of waves there too"). Our base map (OpenFreeMap, the
OpenMapTiles schema) keeps piers but leaves man_made=breakwater out: Liepaja's detached outer breakwater, 2.5 km in the
sea, wasn't there, and the waves ran through it. The map reads these as land (the waves' lee, their fronts, the surf)
and draws them.

OpenStreetMap through the Overpass API (as build_lines.py), the world's (the owner, 19.11.2026: "we have to add it
worldwide ... So we not have same issue in future"): one worldwide query a tag (by the tag's index: ~2 min and
75 MB for the 66,000 breakwaters; the first try asked box by box, 550 zoom-5 squares, and Overpass scans every way in a
box, so 136 squares took the job's 6 hours), cached in breakwaters-cache/. © OpenStreetMap contributors, ODbL.

    tiles/breakwaters/v1/8-x-y.json   the ones in that zoom-8 tile: {"p": [polygon rings ([[lon, lat], ...] outer first)],
                                      "l": [[width m, [[lon, lat], ...]], ...]} (lines: a breakwater 10 m wide, a groyne 4 m)
    tiles/breakwaters/v1/index.json   {"tiles": ["x-y", ...], "zoom": 8, "source": ..., "made": ...}

    python build_breakwaters.py [--local DIR] [--box south,west,north,east (default the world)]
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


def query(tag, box):
    """Every way and relation with that tag (worldwide by the tag's index, or in a box), with its shape."""
    where = f"({box[0]},{box[1]},{box[2]},{box[3]})" if box else ""
    return ('[out:json][timeout:900][maxsize:268435456];('
            f'way["man_made"="{tag}"]{where};relation["man_made"="{tag}"]{where};'
            ");out geom qt;")


def ask(tag, box):
    """One tag's answer (bytes), whole: Overpass says it failed in its "remark" with a 200, or refuses when busy."""
    for attempt in range(9):
        server = OVERPASS[attempt % len(OVERPASS)]
        try:
            data = urllib.parse.urlencode({"data": query(tag, box)}).encode()
            request = urllib.request.Request(server, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=1000) as reply:
                body = reply.read()
            answer = json.loads(body)
            remark = answer.get("remark", "")
            if "error" in remark or "timed out" in remark:
                raise RuntimeError(remark[:120])
            print(f"{tag}: {len(answer['elements'])} from {server.split('/')[2]}, {len(body) / 1e6:.1f} MB", flush=True)
            return body
        except Exception as error:  # busy (429/502/504), or not whole: the next server, waiting a little more each round
            print(f"{tag} ({server.split('/')[2]}): {error}", flush=True)
            time.sleep(60 * (attempt // len(OVERPASS) + 1))
    raise SystemExit(f"{tag}: no server gave a whole answer")


def fetch(box):
    """Each tag's answer, cached; the cache files."""
    CACHE.mkdir(exist_ok=True)
    files = []
    for tag in WIDTH:
        path = CACHE / (f"{tag}.json" if not box else f"{tag}_{'_'.join(f'{v:g}' for v in box)}.json")
        if not path.exists():
            path.write_bytes(ask(tag, box))
        files.append(path)
    return files


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
        for el in json.loads(path.read_bytes())["elements"]:
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
    box = tuple(float(v) for v in args[args.index("--box") + 1].split(",")) if "--box" in args else None
    files = fetch(box)
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
    index = {"tiles": [f"{x}-{y}" for x, y in sorted(tiles)], "zoom": ZOOM, "box": box or "world",
             "source": "OpenStreetMap contributors (ODbL): man_made=breakwater, groyne",
             "made": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    put("index.json", json.dumps(index, separators=(",", ":")).encode())
    print(f"{len(tiles)} tiles, {total / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
