"""Europe's coastline in full detail for the app's weather colours (the owner, 15.10.2026: around
Bergen the islands and fjords were blocks, Natural Earth's 1:10 million shore at zoom 8 at most).

OpenStreetMap's land polygons (osmdata.openstreetmap.de, split, Web Mercator; ODbL, the map already
credits OpenStreetMap) drawn at zoom 10 (about 75 m a pixel in Norway) for each zoom-5 tile over
Europe (25 W to 45 E, 34 to 72 N), Natural Earth's big lakes cut out of it (they stay water, as the
lakes' colours are the sea's), and the distance to the shore worked out as coast_tiles.py does:
127.5 on the shore, 4 levels a zoom-8 pixel inland (up) and out to sea (down), so 1 a zoom-10
pixel. Each zoom-5 tile with a shore is one pack with its zooms 5 to 10 (1365 tiles); all land or
all sea only in the index. The rest of the world keeps Natural Earth's packs (zooms 5 to 8), copied
into the new folder (tiles/coast/v3/, named by the index: the packs are kept a month in caches).

v3 (the owner, 16.10.2026: Tarifa's beach was stairs at zoom 14): near the shore the distance comes
from the land drawn 8 times finer (about 15 m a pixel in the south), a block of 512 zoom-10 pixels
at a time, as each pixel's distance to the nearest fine pixel across the shore; and the tiles hold
16 levels a pixel of their own zoom (127.5 on the shore, so 8 pixels either way), so the phone finds
the shore between the pixels to a sixteenth of one. Only the shore's place and side are read.

v4 (the owner, 16.10.2026: "fine coastline worldwide"; usable anywhere at release): the whole world
the same way (--world), in parts by columns of zoom-5 tiles run side by side (--columns 0,3
--part part.json: their packs uploaded, the part's lists written), then the index from all the
parts (--index DIR). Natural Earth's packs are no longer used.

    python coast_osm.py                upload (INGEST_TOKEN)
    python coast_osm.py --world --columns 0,3 --part part-0.json    one part of the world
    python coast_osm.py --index parts/                               the world's index from its parts
    python coast_osm.py --local DIR    write to DIR (a test; --only 16,8 for one zoom-5 tile)
"""
import io
import json
import math
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import shapefile
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.spatial import cKDTree

import coast_tiles
import world_tiles

OSM = "https://osmdata.openstreetmap.de/download/land-polygons-split-3857.zip"
FILES = "https://files.meteogust.com/tiles/coast/"
FOLDER = "v4"
BASE, TOP, SIZE = 5, 10, 256
EUROPE_X, EUROPE_Y = range(13, 21), range(6, 13)
# The zoom-5 tiles made (Europe's by default; the world's, or a part of it, by the arguments).
X_RANGE, Y_RANGE = EUROPE_X, EUROPE_Y
SPAN = SIZE * 2 ** (TOP - BASE)          # a zoom-5 tile in zoom-10 pixels (8192)
MARGIN = 192                              # zoom-10 pixels round it, so the distance sees past its edge
FINE = 8                                  # the shore's place from the land drawn this much finer
NEAR = 8                                  # zoom-10 pixels either side of the shore worked out finely
LEVELS = 16                               # grey levels a pixel of the tile's own zoom
BLOCK = 512                               # zoom-10 pixels drawn finely at a time
WORLD = 20037508.342789244                # half the Web Mercator square, metres
LIMIT = 85.0511


def download(path):
    if path.exists():
        return path
    print("downloading OSM land polygons...", flush=True)
    request = urllib.request.Request(OSM, headers={"User-Agent": "MeteoGust coastline (data.meteogust.com)"})
    with urllib.request.urlopen(request, timeout=1800) as reply, open(path, "wb") as out:
        while True:
            chunk = reply.read(1 << 22)
            if not chunk:
                break
            out.write(chunk)
    return path


def regions_of(bbox):
    """The zoom-5 tiles over Europe a box (Web Mercator metres) touches, with the margin."""
    n = 2 ** BASE
    pad = MARGIN / SPAN
    x0 = (bbox[0] + WORLD) / (2 * WORLD) * n - pad
    x1 = (bbox[2] + WORLD) / (2 * WORLD) * n + pad
    y0 = (WORLD - bbox[3]) / (2 * WORLD) * n - pad
    y1 = (WORLD - bbox[1]) / (2 * WORLD) * n + pad
    return [(x, y) for x in range(max(int(x0), X_RANGE.start), min(int(x1), X_RANGE.stop - 1) + 1)
            for y in range(max(int(y0), Y_RANGE.start), min(int(y1), Y_RANGE.stop - 1) + 1)]


def load_land(zip_path):
    """Europe's land polygons by zoom-5 tile: their rings as zoom-10 pixel arrays."""
    by_region = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        base = next(n[:-4] for n in names if n.endswith(".shp"))
        reader = shapefile.Reader(shp=io.BytesIO(archive.read(base + ".shp")), shx=io.BytesIO(archive.read(base + ".shx")),
                                  dbf=io.BytesIO(archive.read(base + ".dbf")))
        scale = 2 ** TOP * SIZE / (2 * WORLD)
        count = 0
        for shape in reader.iterShapes():
            regions = regions_of(shape.bbox)
            if not regions:
                continue
            points = np.asarray(shape.points, dtype=np.float64)
            px = (points[:, 0] + WORLD) * scale
            py = (WORLD - points[:, 1]) * scale
            parts = list(shape.parts) + [len(points)]
            rings = [np.stack([px[a:b], py[a:b]], -1) for a, b in zip(parts, parts[1:]) if b - a >= 3]
            for region in regions:
                by_region.setdefault(region, []).append(rings)
            count += 1
        print(f"{count} land polygons over Europe in {len(by_region)} zoom-5 tiles", flush=True)
    return by_region


def signed_area(ring):
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def distance(polygons, lakes, tx, ty):
    """Signed distance to the shore (zoom-10 pixels, land positive) over the tile and its margin;
    None with no land near it, "land" when all land."""
    if not polygons:
        return None
    x0, y0 = tx * SPAN - MARGIN, ty * SPAN - MARGIN
    side = SPAN + 2 * MARGIN
    image = Image.new("L", (side, side), 0)
    draw = ImageDraw.Draw(image)
    for rings in polygons:
        # Shapefile rings: outer ones clockwise with y up, so with y down (pixels) their shoelace area
        # is positive; holes the other way. The outer ones first, then the holes.
        for ring in sorted(rings, key=lambda r: signed_area(r) <= 0):
            pts = [(float(x - x0), float(y - y0)) for x, y in ring]
            draw.polygon(pts, fill=255 if signed_area(ring) > 0 else 0)
    # Natural Earth's lakes stay water.
    if lakes:
        _, _, (lon0, lat0, lon1, lat1) = region_box(tx, ty)
        for rings, box in lakes:
            if box[0] <= lon1 and box[2] >= lon0 and box[1] <= lat1 and box[3] >= lat0:
                draw.polygon(to_pixels(rings[0], x0, y0), fill=0)
                for island in rings[1:]:
                    draw.polygon(to_pixels(island, x0, y0), fill=255)
    land = np.asarray(image) > 127
    if not land.any():
        return None
    inner = land[MARGIN:-MARGIN, MARGIN:-MARGIN]
    if land.all():
        return "land"
    signed = ndimage.distance_transform_edt(land) - ndimage.distance_transform_edt(~land)
    if inner.all() and signed[MARGIN:-MARGIN, MARGIN:-MARGIN].min() > 160:
        return "land"
    coarse = signed[MARGIN:-MARGIN, MARGIN:-MARGIN].astype(np.float32)
    return coarse, refine(coarse, polygons, lakes, tx, ty)


def boxes(rings):
    """A polygon's box (zoom-10 pixels) from its rings."""
    xs = np.concatenate([r[:, 0] for r in rings])
    ys = np.concatenate([r[:, 1] for r in rings])
    return xs.min(), ys.min(), xs.max(), ys.max()


def refine(coarse, polygons, lakes, tx, ty):
    """The distance within NEAR pixels of the shore from the land drawn FINE times finer, block by
    block; farther, the coarse distance (clipped: only the side matters there)."""
    out = np.clip(coarse, -(NEAR + 1), NEAR + 1).astype(np.float32)
    shapes = list(polygons)
    corners = np.array([boxes(rings) for rings in shapes]) if shapes else np.zeros((0, 4))
    lake_rings = []
    if lakes:
        _, _, (lon0, lat0, lon1, lat1) = region_box(tx, ty)
        for rings, box in lakes:
            if box[0] <= lon1 and box[2] >= lon0 and box[1] <= lat1 and box[3] >= lat0:
                lake_rings.append([np.asarray(to_pixels(r, 0, 0)) for r in rings])
    pad = NEAR + 4
    blocks = SPAN // BLOCK
    for by in range(blocks):
        for bx in range(blocks):
            sub = coarse[by * BLOCK:(by + 1) * BLOCK, bx * BLOCK:(bx + 1) * BLOCK]
            near = np.abs(sub) <= NEAR + 1.5
            if not near.any():
                continue
            gx0, gy0 = tx * SPAN + bx * BLOCK - pad, ty * SPAN + by * BLOCK - pad
            gx1, gy1 = gx0 + BLOCK + 2 * pad, gy0 + BLOCK + 2 * pad
            side = (BLOCK + 2 * pad) * FINE
            image = Image.new("L", (side, side), 0)
            draw = ImageDraw.Draw(image)

            def fine(ring):
                return ((ring - (gx0, gy0)) * FINE).ravel().tolist()
            hits = np.nonzero((corners[:, 2] >= gx0) & (corners[:, 0] <= gx1) & (corners[:, 3] >= gy0) & (corners[:, 1] <= gy1))[0]
            for k in hits:
                rings = shapes[k]
                for ring in sorted(rings, key=lambda r: signed_area(r) <= 0):
                    draw.polygon(fine(ring), fill=255 if signed_area(ring) > 0 else 0)
            for rings in lake_rings:
                draw.polygon(fine(rings[0]), fill=0)
                for island in rings[1:]:
                    draw.polygon(fine(island), fill=255)
            land = np.asarray(image) > 127
            land_edge = land & ~ndimage.binary_erosion(land, border_value=1)
            sea_edge = ~land & ndimage.binary_dilation(land)
            if not land_edge.any() or not sea_edge.any():
                continue
            ys, xs = np.nonzero(near)
            qx, qy = (xs + pad + 0.5) * FINE, (ys + pad + 0.5) * FINE
            points = np.stack([qx, qy], -1)
            on_land = land[np.minimum(qy.astype(int), side - 1), np.minimum(qx.astype(int), side - 1)]
            value = np.empty(len(points), dtype=np.float32)
            # A land pixel: how far to the nearest sea pixel across the shore (half a fine pixel
            # short of its centre), and the other way round.
            for mask, edge, sign in ((on_land, sea_edge, 1.0), (~on_land, land_edge, -1.0)):
                if not mask.any():
                    continue
                ey, ex = np.nonzero(edge)
                tree = cKDTree(np.stack([ex + 0.5, ey + 0.5], -1))
                d, _ = tree.query(points[mask], distance_upper_bound=(NEAR + 2) * FINE)
                d = np.where(np.isfinite(d), d, (NEAR + 2) * FINE)
                value[mask] = sign * np.maximum(d - 0.5, 0.05) / FINE
            sub_out = out[by * BLOCK:(by + 1) * BLOCK, bx * BLOCK:(bx + 1) * BLOCK]
            sub_out[ys, xs] = value
    return out


def region_box(tx, ty):
    world = SIZE * 2 ** TOP
    x0, y0 = tx * SPAN - MARGIN, ty * SPAN - MARGIN
    x1, y1 = (tx + 1) * SPAN + MARGIN, (ty + 1) * SPAN + MARGIN
    lon0, lon1 = x0 / world * 360 - 180, x1 / world * 360 - 180

    def lat(y):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world))))
    return x0, y0, (lon0, lat(y1), lon1, lat(y0))


def to_pixels(ring, x0, y0):
    world = SIZE * 2 ** TOP
    lon = ring[:, 0]
    lat = np.clip(ring[:, 1], -LIMIT, LIMIT)
    x = (lon + 180) / 360 * world - x0
    y = (1 - np.log(np.tan(np.radians(lat)) + 1 / np.cos(np.radians(lat))) / math.pi) / 2 * world - y0
    return list(zip(x.tolist(), y.tolist()))


def tiles_of(distances, tx, ty):
    """The tiles at zooms 5 to 10 of a zoom-5 tile, grey: 127.5 on the shore, LEVELS a pixel of the
    tile's own zoom; zooms 9 and 10 from the fine distance, the others from the coarse."""
    coarse, fine = distances
    out = {}
    for z in range(BASE, TOP + 1):
        factor = 2 ** (TOP - z)
        n = SPAN // factor
        signed = fine if z >= TOP - 1 else coarse
        level = signed.reshape(n, factor, n, factor).mean(axis=(1, 3)) if factor > 1 else signed
        grey = np.clip(np.rint(127.5 + level / factor * LEVELS), 0, 255).astype(np.uint8)
        count = 2 ** (z - BASE)
        for j in range(count):
            for i in range(count):
                tile = grey[j * SIZE:(j + 1) * SIZE, i * SIZE:(i + 1) * SIZE]
                out[(z, tx * count + i, ty * count + j)] = world_tiles.png(np.ascontiguousarray(tile), "L")
    return out


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "MeteoGust coastline"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as reply:
                return reply.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def world_index(parts, put):
    """The world's index from its parts' lists (every part must be there)."""
    packs, all_land = [], []
    files = sorted(Path(parts).rglob("*.json"))
    for file in files:
        part = json.loads(file.read_text())
        packs += part["packs"]
        all_land += part["land"]
    columns = sorted({int(name.split("-")[0]) for name in packs + all_land})
    print(f"{len(files)} parts, {len(packs)} packs, {len(all_land)} all land, columns {columns[0]}-{columns[-1]}", flush=True)
    if len(files) < 16 or len(packs) < 300:
        raise SystemExit("parts missing: the index stays as it was")
    index = {"source": "OpenStreetMap land polygons (ODbL), the world, zooms 5-10 (the shore from the land drawn 8 times finer); "
                       "Natural Earth's lakes (public domain)",
             "zooms": [BASE, TOP], "fine": {"zooms": [BASE, TOP], "x": [0, 31], "y": [0, 31]},
             "tileSize": SIZE, "encoding": "127.5 on the shore, inland up and out to sea down, 16 levels a pixel of the tile's zoom",
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "folder": FOLDER,
             "packs": sorted(packs), "land": sorted(all_land)}
    put("index.json", json.dumps(index, separators=(",", ":")).encode())


def main():
    global X_RANGE, Y_RANGE
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    only = tuple(int(v) for v in args[args.index("--only") + 1].split(",")) if "--only" in args else None
    part_file = Path(args[args.index("--part") + 1]) if "--part" in args else None
    world = "--world" in args
    if world:
        X_RANGE, Y_RANGE = range(0, 32), range(0, 32)
        if "--columns" in args:
            first, last = (int(v) for v in args[args.index("--columns") + 1].split(","))
            X_RANGE = range(first, last + 1)
    started = time.time()

    def put(name, data):
        if local:
            local.mkdir(parents=True, exist_ok=True)
            (local / name).write_bytes(data)
        else:
            world_tiles.upload(f"coast/{name}" if name == "index.json" else f"coast/{FOLDER}/{name}", data)

    if "--index" in args:
        world_index(args[args.index("--index") + 1], put)
        return
    land = load_land(download(Path("land-polygons-split-3857.zip")))
    lakes = coast_tiles.polygons("lakes")
    old = json.loads(get(FILES + "index.json"))
    packs, all_land, size = [], [], 0
    europe = {(x, y) for x in X_RANGE for y in Y_RANGE}
    for (tx, ty) in sorted(europe):
        if only and (tx, ty) != only:
            continue
        name = f"{tx}-{ty}"
        result = distance(land.pop((tx, ty), []), lakes, tx, ty)
        if result is None:
            continue
        if isinstance(result, str):
            all_land.append(name)
            continue
        data = world_tiles.encode(tiles_of(result, tx, ty), {z: SIZE for z in range(BASE, TOP + 1)})
        put(f"{BASE}-{name}.pack", data)
        packs.append(name)
        size += len(data)
        print(f"{name}: {len(data) / 1e6:.1f} MB, {time.time() - started:.0f} s", flush=True)
    if only:
        print(f"done {packs} {all_land}")
        return
    if world:
        # A part of the world: its lists for the index (written by the last step from all parts).
        if part_file:
            part_file.write_text(json.dumps({"columns": [X_RANGE.start, X_RANGE.stop - 1], "packs": packs, "land": all_land}))
        print(f"coast v4 columns {X_RANGE.start}-{X_RANGE.stop - 1}: {len(packs)} packs, {len(all_land)} all land, "
              f"{size / 1e6:.0f} MB, {time.time() - started:.0f} s")
        return
    # The rest of the world: Natural Earth's packs as they were.
    for name in old["packs"]:
        tx, ty = (int(v) for v in name.split("-"))
        if (tx, ty) in europe:
            continue
        put(f"{BASE}-{name}.pack", get(FILES + f"{BASE}-{name}.pack"))
        packs.append(name)
    all_land += [name for name in old["land"] if tuple(int(v) for v in name.split("-")) not in europe]
    index = {"source": "OpenStreetMap land polygons (ODbL) over Europe, zooms 5-10 (the shore from the land drawn 8 times finer); "
                       "Natural Earth 1:10m elsewhere, zooms 5-8 (public domain); Natural Earth's lakes",
             "zooms": [BASE, 8], "fine": {"zooms": [BASE, TOP], "x": [EUROPE_X.start, EUROPE_X.stop - 1],
                                          "y": [EUROPE_Y.start, EUROPE_Y.stop - 1]},
             "tileSize": SIZE, "encoding": "127.5 on the shore, inland up and out to sea down: Europe's 16 levels a pixel of "
                                           "the tile's zoom, Natural Earth's 4 a zoom-8 pixel",
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "folder": FOLDER,
             "packs": sorted(packs), "land": sorted(all_land)}
    put("index.json", json.dumps(index, separators=(",", ":")).encode())
    print(f"coast v3: {len(packs)} packs, {len(all_land)} all land, Europe {size / 1e6:.0f} MB, {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
