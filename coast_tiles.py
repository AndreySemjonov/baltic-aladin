"""World coastline tiles: how far each point is from the shore, for the app's world maps.

The weather's colours are stronger over the sea than over land; over the Baltic the app splits
them along its own fine coastline (CoastDistance.png), elsewhere it had only the models' coarse
land (25 km). These tiles give the whole world's shore (the owner, 12.10.2026) from Natural
Earth's 1:10 million land and lakes (public domain; a few hundred metres off at most, plenty up
to regional zoom): zooms 5-8 on the web map's grid, 256-pixel grey tiles, 127.5 on the shore,
4 levels a zoom-8 pixel inland (up) and out to sea (down), so the phone finds a smooth shore.
Each zoom-5 tile with a coast is one pack with its 85 tiles (zoom 5 to 8), as the weather
tiles; zoom-5 tiles all land or all sea are only listed in the index. Made once (the coast
doesn't change), uploaded through station-history as tiles/coast/.

    python coast_tiles.py                upload (INGEST_TOKEN)
    python coast_tiles.py --local DIR    write to DIR (a test; --only 27,12 for one zoom-5 tile)
"""
import json
import math
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

import world_tiles

SOURCE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_{}.geojson"
BASE, TOP, SIZE = 5, 8, 256
# Pixels round each zoom-5 tile's square (zoom 8), so the distance at its edges sees beyond it.
MARGIN = 48
SPAN = SIZE * 2 ** (TOP - BASE)
LIMIT = 85.0511


def polygons(kind):
    """Each polygon's rings (lon, lat arrays; the first the outline, the others holes) and box."""
    with urllib.request.urlopen(urllib.request.Request(SOURCE.format(kind), headers={"User-Agent": "MeteoGust coast"}),
                                timeout=300) as reply:
        data = json.load(reply)
    out = []
    for feature in data["features"]:
        geometry = feature["geometry"]
        if geometry is None:
            continue
        parts = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for part in parts:
            rings = [np.asarray(ring, dtype=np.float64) for ring in part if len(ring) >= 3]
            if not rings:
                continue
            outline = rings[0]
            box = (outline[:, 0].min(), outline[:, 1].min(), outline[:, 0].max(), outline[:, 1].max())
            out.append((rings, box))
    return out


def to_pixels(ring, x0, y0):
    """Zoom-8 pixels of a ring (lon, lat), from the region's corner."""
    world = SIZE * 2 ** TOP
    lon = ring[:, 0]
    lat = np.clip(ring[:, 1], -LIMIT, LIMIT)
    x = (lon + 180) / 360 * world - x0
    y = (1 - np.log(np.tan(np.radians(lat)) + 1 / np.cos(np.radians(lat))) / math.pi) / 2 * world - y0
    return list(zip(x.tolist(), y.tolist()))


def region_box(tx, ty):
    """The zoom-5 tile's square with its margin, as longitudes and latitudes."""
    world = SIZE * 2 ** TOP
    x0, y0 = tx * SPAN - MARGIN, ty * SPAN - MARGIN
    x1, y1 = (tx + 1) * SPAN + MARGIN, (ty + 1) * SPAN + MARGIN
    lon0, lon1 = x0 / world * 360 - 180, x1 / world * 360 - 180
    def lat(y):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world))))
    return x0, y0, (lon0, lat(y1), lon1, lat(y0))


def distance(land_polygons, lake_polygons, tx, ty):
    """The signed distance to the shore (zoom-8 pixels, land positive) over the tile and its
    margin; None when there's no land near it, "land" when it's all land."""
    x0, y0, (lon0, lat0, lon1, lat1) = region_box(tx, ty)
    def near(box):
        return box[0] <= lon1 and box[2] >= lon0 and box[1] <= lat1 and box[3] >= lat0
    lands = [p for p in land_polygons if near(p[1])]
    if not lands:
        return None
    side = SPAN + 2 * MARGIN
    image = Image.new("L", (side, side), 0)
    draw = ImageDraw.Draw(image)
    for rings, _ in lands:
        draw.polygon(to_pixels(rings[0], x0, y0), fill=255)
        for hole in rings[1:]:
            draw.polygon(to_pixels(hole, x0, y0), fill=0)
    for rings, _ in (p for p in lake_polygons if near(p[1])):
        draw.polygon(to_pixels(rings[0], x0, y0), fill=0)
        for island in rings[1:]:
            draw.polygon(to_pixels(island, x0, y0), fill=255)
    land = np.asarray(image) > 127
    inner = land[MARGIN:-MARGIN, MARGIN:-MARGIN]
    if not land.any():
        return None
    if land.all():
        return "land"
    signed = ndimage.distance_transform_edt(land) - ndimage.distance_transform_edt(~land)
    if inner.all() and signed[MARGIN:-MARGIN, MARGIN:-MARGIN].min() > 40:
        return "land"
    return signed[MARGIN:-MARGIN, MARGIN:-MARGIN]


def tiles_of(signed, tx, ty):
    """The 85 tiles (zoom 5 to 8) of a zoom-5 tile from its zoom-8 distances, as grey PNGs."""
    out = {}
    for z in range(BASE, TOP + 1):
        factor = 2 ** (TOP - z)
        if factor > 1:
            n = SPAN // factor
            level = signed.reshape(n, factor, n, factor).mean(axis=(1, 3))
        else:
            level = signed
        grey = np.clip(np.rint(127.5 + level * 4), 0, 255).astype(np.uint8)
        count = 2 ** (z - BASE)
        for j in range(count):
            for i in range(count):
                tile = grey[j * SIZE:(j + 1) * SIZE, i * SIZE:(i + 1) * SIZE]
                out[(z, tx * count + i, ty * count + j)] = world_tiles.png(np.ascontiguousarray(tile), "L")
    return out


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    only = tuple(int(v) for v in args[args.index("--only") + 1].split(",")) if "--only" in args else None
    started = time.time()
    lands, lakes = polygons("land"), polygons("lakes")
    print(f"{len(lands)} land polygons, {len(lakes)} lakes")
    packs, all_land, size = [], [], 0
    n = 2 ** BASE
    for ty in range(n):
        for tx in range(n):
            if only and (tx, ty) != only:
                continue
            result = distance(lands, lakes, tx, ty)
            if result is None:
                continue
            name = f"{tx}-{ty}"
            if isinstance(result, str):
                all_land.append(name)
                continue
            data = world_tiles.encode(tiles_of(result, tx, ty), {z: SIZE for z in range(BASE, TOP + 1)})
            # The pack's header names its zooms (5-8) and tile sizes.
            if local:
                local.mkdir(parents=True, exist_ok=True)
                (local / f"{BASE}-{name}.pack").write_bytes(data)
            else:
                world_tiles.upload(f"coast/{BASE}-{name}.pack", data)
            packs.append(name)
            size += len(data)
        print(f"row {ty}: {len(packs)} packs, {len(all_land)} all land, {time.time() - started:.0f} s", flush=True)
    index = {"source": "Natural Earth 1:10m land and lakes (public domain)", "zooms": [BASE, TOP], "tileSize": SIZE,
             "encoding": "127.5 on the shore, + 4 a zoom-8 pixel inland, - 4 out to sea",
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "packs": packs, "land": all_land}
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (local / "index.json").write_bytes(body)
    else:
        print(world_tiles.upload("coast/index.json", body).strip())
    print(f"coast: {len(packs)} packs, {len(all_land)} all land, {size / 1e6:.0f} MB, {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
