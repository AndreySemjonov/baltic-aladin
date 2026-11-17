"""The sea's depth near the coasts for the web map's wave crests (the owner, 17.11.2026: the crests slow,
turn and break over the seabed near the beaches): tiles/depth/<folder>/, packed as the coast's
(coast_osm.py: one pack a zoom-5 square, 5-x-y.pack; coast_low.py: zooms 2-4 in low.pack; index.json).

Web Mercator tiles, 256 px, grey: 0 land or unknown; the sea 1 + sqrt(min(depth, 200 m) / 200) x 254
(255 = 200 m or deeper; the square root gives fine steps near the beach). Only tiles with sea shallower
than 200 m are kept: a missing tile is deep sea or land (the coast tiles tell which).

Sources: EMODnet Bathymetry's DTM (Europe, 36 W-43 E, 15-90 N, 1/16 arc-minute ~115 m, CC BY 4.0;
its WCS, a window at a time) where it has data, zooms 5-10; GEBCO's global grid elsewhere (15 arc-
seconds ~460 m, public domain), zooms 5-8 (zoom 8's pixel is ~600 m, finer adds nothing). Their
datums differ: EMODnet's depths are below Lowest Astronomical Tide, GEBCO's below mean sea level (not
corrected: a metre or two at most, where tides are big). Each tile pixel takes the source grid
bilinearly, its cells first averaged when finer than the pixel; a pixel is sea where most of it is.

GEBCO: GEBCO_2026.nc (BODC via CEDA, 7.5 GB, netCDF4/HDF5; the elevation is its last 7.46 GB, one
contiguous uncompressed little-endian int16 array, rows from the south: h5py's offset, 17.11.2026),
read as a memory map from a local copy (--gebco FILE, downloaded first) or by byte ranges from CEDA
(--gebco URL; slow, for tests).

    curl -L -o GEBCO_2026.nc https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026/ice_surface_elevation/netcdf/GEBCO_2026.nc
    python depth_tiles.py --gebco GEBCO_2026.nc                       the world, uploaded (INGEST_TOKEN)
    python depth_tiles.py --gebco URL --bbox -34.2,18.0,-33.6,18.7 --local DIR    a test (s,w,n,e)
"""
import io
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import world_tiles

GEBCO_URL = "https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026/ice_surface_elevation/netcdf/GEBCO_2026.nc"
EMODNET = ("https://ows.emodnet-bathymetry.eu/wcs?service=WCS&version=1.0.0&request=GetCoverage&coverage=emodnet:mean"
           "&crs=EPSG:4326&BBOX={w},{s},{e},{n}&format=GeoTIFF&resx={r}&resy={r}")
EMOD_STEP = 1 / 960                       # 1/16 arc-minute
EMOD_BOX = (-36.0, 15.0, 43.0, 90.0)      # w, s, e, n
FOLDER = "v1"
BASE, COARSE, FINE, SIZE = 5, 8, 10, 256
DEEP = 200.0
UA = {"User-Agent": "MeteoGust depth tiles (data.meteogust.com)"}


def get(url, headers=None, timeout=300):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=timeout) as reply:
                return reply.read()
        except OSError:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


class Gebco:
    ROWS, COLS, STEP = 43200, 86400, 1 / 240

    def __init__(self, source):
        size = self.ROWS * self.COLS * 2
        if source.startswith("http"):
            self.url, self.data = source, None
            length = int(urllib.request.urlopen(urllib.request.Request(source, headers=UA, method="HEAD"), timeout=60)
                         .headers["Content-Length"])
            self.offset = length - size
        else:
            self.data = np.memmap(source, "<i2", "r", offset=Path(source).stat().st_size - size, shape=(self.ROWS, self.COLS))
        # The layout read right: the Pacific deep, the Alps high.
        pacific, alps = self.rows(self.row(0.0), self.row(0.0) + 1, self.col(-150.0), self.col(-150.0) + 1), \
            self.rows(self.row(45.833), self.row(45.833) + 1, self.col(6.865), self.col(6.865) + 1)
        assert pacific[0, 0] < -3000 and alps[0, 0] > 3000, f"GEBCO read wrong: {pacific}, {alps}"

    def row(self, lat):
        return int((lat + 90) / self.STEP)

    def col(self, lon):
        return int((lon + 180) / self.STEP)

    def rows(self, j0, j1, i0, i1):
        if self.data is not None:
            return np.asarray(self.data[j0:j1, i0:i1])

        def one(j):
            start = self.offset + (j * self.COLS + i0) * 2
            return np.frombuffer(get(self.url, {"Range": f"bytes={start}-{start + (i1 - i0) * 2 - 1}"}, 60), "<i2")
        with ThreadPoolExecutor(8) as pool:
            return np.stack(list(pool.map(one, range(j0, j1))))

    def window(self, w, s, e, n):
        """The elevation (m, rows from the south) round a box, its first cell's centre and its step."""
        # shortcut: clamped at the antimeridian, no wrap; fine as the zoom-5 squares' edges are on it.
        j0, j1 = max(self.row(s) - 2, 0), min(self.row(n) + 3, self.ROWS)
        i0, i1 = max(self.col(w) - 2, 0), min(self.col(e) + 3, self.COLS)
        elevation = self.rows(j0, j1, i0, i1).astype(np.float32)
        return elevation, -180 + (i0 + 0.5) * self.STEP, -90 + (j0 + 0.5) * self.STEP, self.STEP


def emodnet(w, s, e, n):
    """EMODnet's elevation round a box (as Gebco.window), or None outside it. Snapped to its own grid
    (1/960 degree from whole degrees), so the WCS hands its cells as they are."""
    w, s = max(math.floor(w * 960) - 2, EMOD_BOX[0] * 960) / 960, max(math.floor(s * 960) - 2, EMOD_BOX[1] * 960) / 960
    e, n = min(math.ceil(e * 960) + 2, EMOD_BOX[2] * 960) / 960, min(math.ceil(n * 960) + 2, EMOD_BOX[3] * 960) / 960
    if e <= w or n <= s:
        return None
    import tifffile
    data = get(EMODNET.format(w=w, s=s, e=e, n=n, r=EMOD_STEP))
    elevation = tifffile.imread(io.BytesIO(data)).astype(np.float32)[::-1]   # rows from the south
    elevation[~(elevation > -1e4)] = np.nan                                   # its nodata is very negative
    return elevation, w + EMOD_STEP / 2, s + EMOD_STEP / 2, EMOD_STEP


def sample(window, lons, lats):
    """A source window on the pixels' centres: (how much is known, how much is sea, the sea's mean
    depth), each from 0 to 1 but the depth; the cells first averaged over a pixel when finer."""
    from scipy import ndimage
    elevation, lon0, lat0, step = window
    known = np.isfinite(elevation)
    sea = known & (elevation < 0)
    layers = [known.astype(np.float32), sea.astype(np.float32), np.where(sea, -elevation, 0).astype(np.float32)]
    pixel_lon = abs(lons[1] - lons[0])
    pixel_lat = abs(lats[len(lats) // 2] - lats[len(lats) // 2 - 1])
    box = (max(1, int(round(pixel_lat / step))), max(1, int(round(pixel_lon / step))))
    if box != (1, 1):
        layers = [ndimage.uniform_filter(layer, box, mode="nearest") for layer in layers]
    y, x = np.meshgrid((lats - lat0) / step, (lons - lon0) / step, indexing="ij")
    k, s, d = (ndimage.map_coordinates(layer, [y, x], order=1, mode="nearest") for layer in layers)
    with np.errstate(invalid="ignore", divide="ignore"):
        return k, s / k, d / s


def block(gebco, top, x0, y0, n):
    """The depth (m; NaN not sea) of n x n pixels of zoom `top` from x0, y0, and whether EMODnet's
    were used; None where it's all land or all deep."""
    world = SIZE * 2 ** top
    lons = (np.arange(x0, x0 + n) + 0.5) / world * 360 - 180
    lats = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (np.arange(y0, y0 + n) + 0.5) / world))))
    w, e = x0 / world * 360 - 180, (x0 + n) / world * 360 - 180
    n_, s = (math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world)))) for y in (y0, y0 + n))
    window = gebco.window(w, s, e, n_)
    # All high land (lagoons and fjords are under 10 m in GEBCO's cells) or all deep: nothing here.
    if window[0].min() >= 10 or window[0].max() <= -DEEP:
        return None
    _, sea, depth = sample(window, lons, lats)
    out = np.where(sea >= 0.5, depth, np.nan).astype(np.float32)
    fine = False
    if top == FINE:
        window = emodnet(w, s, e, n_)
        if window is not None and np.isfinite(window[0]).any():
            known, sea, depth = sample(window, lons, lats)
            known = known >= 0.5
            out = np.where(known, np.where(sea >= 0.5, depth, np.nan), out).astype(np.float32)
            fine = bool(known.any())
    return out, fine


def grey(depth):
    with np.errstate(invalid="ignore"):
        return np.where(np.isfinite(depth), 1 + np.rint(np.sqrt(np.clip(depth, 0, DEEP) / DEEP) * 254), 0).astype(np.uint8)


def coarser(depth, factor):
    """The mean depth of the sea's pixels in each factor x factor (NaN where none is sea)."""
    m = depth.shape[0] // factor
    sea = np.isfinite(depth).reshape(m, factor, m, factor).sum(axis=(1, 3))
    total = np.nan_to_num(depth).reshape(m, factor, m, factor).sum(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sea > 0, total / sea, np.nan).astype(np.float32)


def tiles_of(depth, z, tx, ty, tiles):
    """The tiles of one zoom from a square's depth, only those with sea shallower than DEEP."""
    count = depth.shape[0] // SIZE
    level = grey(depth)
    for j in range(count):
        for i in range(count):
            tile = level[j * SIZE:(j + 1) * SIZE, i * SIZE:(i + 1) * SIZE]
            if ((tile > 0) & (tile < 255)).any():
                tiles[(z, tx * count + i, ty * count + j)] = world_tiles.png(np.ascontiguousarray(tile), "L")


def square(gebco, tx, ty, bbox, pool):
    """A zoom-5 square's tiles ({(z, x, y): PNG}), whether it goes to zoom 10, and its zoom-5 depth."""
    lon_w, lon_e = tx / 32 * 360 - 180, (tx + 1) / 32 * 360 - 180
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / 32))))
    top = FINE if lon_w < EMOD_BOX[2] and lon_e > EMOD_BOX[0] and lat_n > EMOD_BOX[1] else COARSE
    n = SIZE * 2 ** (top - COARSE)                       # a zoom-8 tile in pixels of `top`
    count = 2 ** (COARSE - BASE)
    jobs = []
    for j in range(count):
        for i in range(count):
            x, y = tx * count + i, ty * count + j          # the zoom-8 tile
            w, e = x / 256 * 360 - 180, (x + 1) / 256 * 360 - 180
            north, south = (math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * v / 256)))) for v in (y, y + 1))
            if bbox and (e < bbox[1] or w > bbox[3] or north < bbox[0] or south > bbox[2]):
                continue
            jobs.append((i, j))
    results = list(pool.map(lambda ij: block(gebco, top, (tx * count + ij[0]) * n, (ty * count + ij[1]) * n, n), jobs))
    if all(result is None for result in results):
        return {}, False, None
    depth = np.full((count * n, count * n), np.nan, np.float32)
    fine = False
    for (i, j), result in zip(jobs, results):
        if result is not None:
            depth[j * n:(j + 1) * n, i * n:(i + 1) * n] = result[0]
            fine |= result[1]
    tiles = {}
    last = FINE if fine else COARSE
    for z in range(BASE, last + 1):
        factor = 2 ** (top - z)
        tiles_of(coarser(depth, factor) if factor > 1 else depth, z, tx, ty, tiles)
    return tiles, fine, coarser(depth, 2 ** (top - BASE))


def low(zoom5):
    """Zooms 2-4 from the zoom-5 depths ({(x, y): array}), the sea's pixels averaged as above.
    shortcut: a square with no pack counts as not sea (deep ocean too), so the shelf's edge reads a
    little shallow at zooms 2-4; fine for a world view."""
    tiles, level = {}, zoom5
    for z in (4, 3, 2):
        upper = {}
        for x, y in {(x // 2, y // 2) for x, y in level}:
            full = np.full((2 * SIZE, 2 * SIZE), np.nan, np.float32)
            for dx in (0, 1):
                for dy in (0, 1):
                    if (2 * x + dx, 2 * y + dy) in level:
                        full[dy * SIZE:(dy + 1) * SIZE, dx * SIZE:(dx + 1) * SIZE] = level[(2 * x + dx, 2 * y + dy)]
            upper[(x, y)] = coarser(full, 2)
        level = upper
        for (x, y), depth in level.items():
            tiles_of(depth, z, x, y, tiles)
    return tiles


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    source = args[args.index("--gebco") + 1] if "--gebco" in args else "GEBCO_2026.nc"
    bbox = tuple(float(v) for v in args[args.index("--bbox") + 1].split(",")) if "--bbox" in args else None   # s, w, n, e
    started = time.time()

    def put(name, data):
        if local:
            local.mkdir(parents=True, exist_ok=True)
            (local / name).write_bytes(data)
        else:
            world_tiles.upload(f"depth/{name}" if name == "index.json" else f"depth/{FOLDER}/{name}", data)

    gebco = Gebco(source)
    packs, fine, zoom5, size = [], [], {}, 0
    with ThreadPoolExecutor(4) as pool:
        for ty in range(32):
            for tx in range(32):
                if bbox:
                    w, e = tx / 32 * 360 - 180, (tx + 1) / 32 * 360 - 180
                    north, south = (math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * v / 32)))) for v in (ty, ty + 1))
                    if e < bbox[1] or w > bbox[3] or north < bbox[0] or south > bbox[2]:
                        continue
                tiles, to_fine, depth = square(gebco, tx, ty, bbox, pool)
                if not tiles:
                    continue
                name = f"{tx}-{ty}"
                last = FINE if to_fine else COARSE
                data = world_tiles.encode(tiles, {z: SIZE for z in range(BASE, last + 1)})
                put(f"{BASE}-{name}.pack", data)
                packs.append(name)
                if to_fine:
                    fine.append(name)
                zoom5[(tx, ty)] = depth
                size += len(data)
                print(f"{name}: zooms {BASE}-{last}, {len(tiles)} tiles, {len(data) / 1e6:.1f} MB, "
                      f"{time.time() - started:.0f} s", flush=True)
    body = world_tiles.encode(low(zoom5), {z: SIZE for z in (2, 3, 4)})
    put("low.pack", body)
    index = {"source": "EMODnet Bathymetry DTM (CC BY 4.0; depth below Lowest Astronomical Tide) over Europe, zooms 5-10; "
                       "GEBCO_2026 Grid (public domain; below mean sea level) elsewhere, zooms 5-8; zooms 2-4 in low.pack",
             "zooms": [BASE, FINE], "fine": sorted(fine), "tileSize": SIZE,
             "encoding": "0 land or unknown; sea 1 + sqrt(min(depth m, 200) / 200) * 254, 255 = 200 m or deeper; "
                         "a missing tile is deep sea or land",
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "folder": FOLDER, "packs": sorted(packs)}
    put("index.json", json.dumps(index, separators=(",", ":")).encode())
    print(f"depth {FOLDER}: {len(packs)} packs ({len(fine)} to zoom 10), {size / 1e6:.0f} MB, low.pack "
          f"{len(body) / 1e6:.1f} MB, {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
