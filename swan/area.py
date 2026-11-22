"""An area for SWAN's tables (the owner, 19.11.2026: "Yes, build it"; WindguruStationWatch docs/backlog/design.md "The
waves engine"): the sea bed and the land over a box round a spot, made from our own files the way the web map's coarse
fronts grid makes them (WindguruStationWatch tools/webmap/smooth.js FRAGMENT_REFRACT's depthAt with uBig, and
breakwaterLand), so SWAN sees the coast and walls the map draws:
  - the coast (tiles/coast, v5: 127.5 on the shore, 16 levels a pixel, land up; squares all land listed, all sea none):
    the distance to the shore at each cell's middle, read between the zoom-10 pixels; unknown as open sea;
  - the depth (tiles/depth: 1 + sqrt(depth / 200 m) x 254, 0 land or unknown; the finest zoom a square's pack has):
    near the shore (200-800 m) blended with a beach (0.2 + 0.12 d^(2/3)), where unknown the beach alone;
  - land where the coast says so, the bed under 0.1 m, or a breakwater or groyne (tiles/breakwaters) within 3/4 of a
    cell of the cell's middle;
  - ESA WorldCover 2021's 10 m pixels (_worldcover): the shore, land cells it mostly sees as open water made sea (our
    coast's zoom-10 pixels put Haademeeste's beach ~200 m too far out); reeds, sea cells it mostly calls plants (reed
    islands, a reed belt), -2: they stand above the water at every level and leave flat water behind.
The water level is SWAN's (tables.py SET LEVEL).

  python3 swan/area.py LAT LON [SIDE_M] [CELLS]   prints the grid's sums (a check), e.g. 56.5505 20.986
"""
import gzip
import io
import json
import math
import struct
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

FILES = "https://files.meteogust.com/tiles/"
# ESA WorldCover 2021 (10 m, CC BY 4.0), 3-degree tiles named by their south-west corner, on AWS.
WORLDCOVER = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif"
# Its plants: trees, shrubs, grass, herbaceous wetland (reeds), mangroves, moss and lichen.
GREEN = (10, 20, 30, 90, 95, 100)
# The water reeds shut in, at most this deep (m) at the mean level (_worldcover).
LAGOON = 0.2
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


def build(box, W, H, outlines=None, bars=None, cover=None):
    """The depth (m, H x W, row 0 the box's north edge; -1 land, -2 reeds) over box (world 0-1: x, y, width, height);
    the reeds' outlines added to outlines (a list) if given (_worldcover); a beach's sandbars and outlined shallows if given
    (bars.json: _bars, _shallows); cover (H x W floats) if given gets the satellite bed's share of each cell (_bed)."""
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
    opened = _worldcover(depth, box, W, H, outlines, river=(bars or {}).get("river", 0))
    shore = depth < 0
    _breakwaters(depth, box, W, H)
    if bars and bars.get("bars"):
        # (The breakwaters' own cells, land before them or not: Pavilosta's moles are in the coast already.)
        walls = np.zeros_like(depth)
        _breakwaters(walls, box, W, H)
        river = depth[opened].copy() if bars.get("river") else None
        _bars(depth, box, W, H, bars, shore & (walls == 0), walls < 0)
        # (None carved up a river: by the distance to its banks they ran down the channel's middle.)
        if river is not None:
            depth[opened] = river
    if bars and bars.get("shelf"):
        _shelf(depth, box, W, H, bars, shore)
    if bars and bars.get("bed"):
        _bed(depth, box, W, H, bed_for(bars["bed"]), cover)
    if bars and bars.get("shallows"):
        _shallows(depth, box, W, H, bars["shallows"])
    return depth


BEDS = Path(__file__).resolve().parent / "beds"


def bed_for(name):
    """A satellite bed (beds/NAME.json + NAME.bin.gz, survey.py): {"box": world 0-1 [x, y, width, height], "W", "H",
    "base", "step"} and a byte a pixel (rows from the north): base + step x byte metres below mean sea level (negative:
    above it), 255 none. As "z": metres, NaN none, and "w" its weight 0-1 (1 a reading or near one)."""
    meta = json.loads((BEDS / f"{name}.json").read_bytes().decode())
    b = np.frombuffer(gzip.decompress((BEDS / f"{name}.bin.gz").read_bytes()), np.uint8).reshape(meta["H"], meta["W"])
    z = np.where(b == 255, np.nan, b * meta["step"] + meta["base"]).astype(np.float32)
    lat = math.atan(math.sinh(math.pi * (1 - 2 * (meta["box"][1] + meta["box"][3] / 2))))
    z, w = _bed_fill(z, meta["box"][2] * EARTH * math.cos(lat) / meta["W"])
    return dict(meta, z=z, w=w)


def _bed_fill(z, px):
    """A bed's readings (NaN none) on px-metre pixels: (z filled, its weight 0-1).
    Water without a reading (a dark weed patch, a cloud's speckle, the band between a bar and the reeds too dark to
    read, the beach's rim) filled from the readings round it, finest first, to FILL_M from one: left empty, the old
    ~100 m depth tiles showed through as deep squares on the bank (21.11.2026, Berzciems), then as stepped blocks
    beside Haademeeste's reed islands (the owner, 22.11.2026: "make it according our automatic approach", "Yes, all
    three"). Its weight fades from NEAR_M to FILL_M from a reading, and over EDGE_M inside the window's own edge, so
    the tiles take over gradually (a straight line across the shallows at the window's edge otherwise). Water too
    deep to read borders ~2.4 m readings, which _bed doesn't trust anyway, so the tiles still decide there."""
    from scipy.ndimage import distance_transform_edt, gaussian_filter

    ok = ~np.isnan(z)
    dist = distance_transform_edt(~ok) * px
    filled, zz, m = z.copy(), np.where(ok, z, 0.0), ok.astype(np.float32)
    for s in (20, 40, 80, 160):
        num, den = gaussian_filter(zz, s / px), gaussian_filter(m, s / px)
        take = np.isnan(filled) & (den > 0.02)
        filled[take] = num[take] / den[take]
    # (the scales' seams evened: one pass over the filled pixels only, the readings as they are)
    have = np.isfinite(filled)
    num, den = gaussian_filter(np.where(have, filled, 0.0), 30 / px), gaussian_filter(have.astype(np.float32), 30 / px)
    soft = ~ok & have & (den > 0.2)
    filled[soft] = num[soft] / den[soft]
    w = np.clip((FILL_M - dist) / (FILL_M - NEAR_M), 0, 1)
    H, W = z.shape
    yy, xx = np.ogrid[:H, :W]
    edge = np.minimum(np.minimum(yy, H - 1 - yy), np.minimum(xx, W - 1 - xx)) * px
    w = (w * np.clip(edge / EDGE_M, 0, 1)).astype(np.float32)
    # (No weight where the fill found nothing: a lone reading is too little for it, but its 250 m of weight round it
    # made _bed take the empty cells as 0 m: rings of "dry" water with the reading's own depth in the middle, ~1 km off
    # Engure and Berzciems along the survey's 1.2 km edge; the owner, 22.11.2026: "this circles near engure/berzciems.
    # What is this?")
    w = np.where(np.isfinite(filled), w, 0).astype(np.float32)
    return np.where(w > 0, filled, np.nan).astype(np.float32), w


NEAR_M, FILL_M, EDGE_M = 70.0, 250.0, 250.0


def _bed(depth, box, W, H, bed, cover=None):
    """A satellite bed (bed_for) over the grid's sea cells (the owner, 20.11.2026, the satellite's depth picture: "this is
    what we need to implement in our sandbank", then "Yes, do A"): each cell the mean of the bed's pixels in it (k x k
    points; a grid finer than the bed takes the nearest), blended in by the share it covers and trusted to ~1.2 m, our
    depth tiles' by 2 m (the Baltic's brown water hides the bottom below ~1.5-2 m). Land stays land; a bed above mean sea
    level is wet above its height (-0.9 at most: -1 is land)."""
    bb, bw, bh, z, wt = bed["box"], bed["W"], bed["H"], bed["z"], bed["w"]
    k = max(1, int(round(box[2] / W / (bb[2] / bw) * 1.5)))
    total, n, wsum = np.zeros((H, W)), np.zeros((H, W)), np.zeros((H, W))
    for sy in (np.arange(k) + 0.5) / k:
        rr = np.floor(((box[1] + (np.arange(H) + sy) / H * box[3]) - bb[1]) / bb[3] * bh).astype(int)
        for sx in (np.arange(k) + 0.5) / k:
            cc = np.floor(((box[0] + (np.arange(W) + sx) / W * box[2]) - bb[0]) / bb[2] * bw).astype(int)
            R, C = np.meshgrid(rr, cc, indexing="ij")
            inside = (R >= 0) & (R < bh) & (C >= 0) & (C < bw)
            v = np.full((H, W), np.nan, np.float32)
            v[inside] = z[R[inside], C[inside]]
            ok = np.isfinite(v)
            total += np.where(ok, v, 0.0)
            n += ok
            wsum[inside] += wt[R[inside], C[inside]]
    zb = total / np.maximum(n, 1)
    wsum = np.where(n > 0, wsum, 0)  # (a cell without a reading isn't 0 m: _bed_fill's lone readings, the rings)
    t = np.clip((zb - 1.2) / 0.8, 0, 1)
    trust = wsum / (k * k) * (1 - t * t * (3 - 2 * t))
    sea = depth > -1
    depth[sea] = np.maximum(depth[sea] * (1 - trust[sea]) + zb[sea] * trust[sea], -0.9)
    # (Where the satellite read, at any depth: the map draws the depth only there, the owner, 22.11.2026, "Yes, A + C".)
    if cover is not None:
        cover[:] = wsum / (k * k)


def _shallows(depth, box, W, H, shallows):
    """Outlined shallows at the depth they usually have (bars.json "shallows": [{"name", "depth", "ring": [[lat, lon], ...]}]):
    the sea cells whose middles are inside an outline at most that deep, e.g. a lagoon behind an offshore sandbank and the
    bank's crest (the owner, 20.11.2026, Berzciems: "A sandbank offshore"), where the depth tiles' ~100 m pixels have it
    metres too deep. A depth below 0 is a bed above mean sea level (to -0.9 m: -1 is land): dry below that level, wet above
    ("Usually bank appears at 20-30cm water level": -0.25)."""
    from PIL import Image, ImageDraw

    for s in shallows:
        assert -0.9 <= s["depth"], s
        img = Image.new("L", (W, H), 0)
        pts = []
        for lat, lon in s["ring"]:
            my = (1 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi) / 2
            pts.append(((((lon + 180) / 360) - box[0]) / box[2] * W - 0.5, (my - box[1]) / box[3] * H - 0.5))
        ImageDraw.Draw(img).polygon(pts, fill=1)
        inside = (np.asarray(img) > 0) & (depth >= 0)
        depth[inside] = np.minimum(depth[inside], s["depth"])


def _bars(depth, box, W, H, bars, shore, walls):
    """A beach's longshore sandbars (bars.json: {"at": [lat, lon], "bars": [[metres from the waterline, crest depth], ...]};
    the owner, 20.11.2026: "Yes, add the bars"): a ridge ~60 m wide along the shore at each distance, the bed raised to the
    crest's depth where deeper, and a trough ~0.4 m deeper ~55 m landward of it; within ~2 km of the beach measured (gone by
    2.5 km), never within 250 m of a breakwater (walls: a harbour's mouth). The distance from the shore without the
    breakwaters (shore: land and reeds, not the walls), as the pictures' waterline."""
    from scipy.ndimage import distance_transform_edt

    lat_of = lambda my: np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * my))))  # noqa: E731
    lats = lat_of(box[1] + (np.arange(H) + 0.5) / H * box[3])
    lons = (box[0] + (np.arange(W) + 0.5) / W * box[2]) * 360 - 180
    cell = box[2] * EARTH * math.cos(math.radians(float(lats.mean()))) / W
    d = distance_transform_edt(~shore) * cell
    away = distance_transform_edt(~walls) * cell if walls.any() else np.full(depth.shape, 1e9)
    lat0, lon0 = bars["at"]
    north = (lats[:, None] - lat0) * 111195.0
    east = (lons[None, :] - lon0) * 111195.0 * math.cos(math.radians(lat0))
    near = np.hypot(north, east)
    t = np.clip((near - 2000.0) / 500.0, 0, 1)
    w = (1 - t * t * (3 - 2 * t)) * (depth >= 0) * (away > 250)
    # shortcut: a harbour whose moles aren't in the breakwater tiles (Pavilosta) kept clear by hand; the tiles if more come.
    for clat, clon, r in bars.get("clear", []):
        w[np.hypot((lats[:, None] - clat) * 111195.0, (lons[None, :] - clon) * 111195.0 * math.cos(math.radians(clat))) < r] = 0
    for x, crest in bars["bars"]:
        ridge = np.exp(-(((d - x) / 30.0) ** 2))
        trough = 0.4 * np.exp(-(((d - (x - 55.0)) / 22.0) ** 2))
        depth[:] = np.where(w > 0, depth - w * np.maximum(depth - crest, 0) * ridge + w * trough, depth)


def _shelf(depth, box, W, H, bars, shore):
    """A beach's profile where our depths are wrong and the satellite can't see (bars.json "shelf": [[metres from the
    waterline, depth], ...] round "at"; the owner, 21.11.2026, Parnu: "far away shallow", where our grid had 1.9 m within
    100 m and 3.7 m at 200-400): the sea no deeper than the profile within ~2 km of "at" (gone by 2.5 km)."""
    from scipy.ndimage import distance_transform_edt

    lat_of = lambda my: np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * my))))  # noqa: E731
    lats = lat_of(box[1] + (np.arange(H) + 0.5) / H * box[3])
    lons = (box[0] + (np.arange(W) + 0.5) / W * box[2]) * 360 - 180
    cell = box[2] * EARTH * math.cos(math.radians(float(lats.mean()))) / W
    d = distance_transform_edt(~shore) * cell
    lat0, lon0 = bars["at"]
    near = np.hypot((lats[:, None] - lat0) * 111195.0, (lons[None, :] - lon0) * 111195.0 * math.cos(math.radians(lat0)))
    t = np.clip((near - 2000.0) / 500.0, 0, 1)
    w = (1 - t * t * (3 - 2 * t)) * (depth >= 0)
    xs, hs = zip(*bars["shelf"])
    cap = np.interp(d, xs, hs)
    depth[:] = np.where((w > 0) & (depth > cap), depth - w * (depth - cap), depth)


def _worldcover(depth, box, W, H, outlines=None, sub=3, river=0):
    """The shore and the reeds from ESA WorldCover (sub x sub points a cell):
      - land cells mostly open water there made sea (the owner, 19.11.2026: "take the shoreline from WorldCover too":
        Haademeeste's lagoon one cell wide), the depth a beach's (as build's) from WorldCover's own shore; its bare sand
        not land (the picture may be at low water);
      - sea cells mostly plants there land, -2 (the owner, 19.11.2026, Haademeeste: "There is reeds what block chop and
        make flat water behind it ... usually it is only green land/grass/reed what blocks chop"). OpenStreetMap had the
        reed islands only as a marsh with its lagoon in, and 7 ha of islets; WorldCover the islands, the lagoon water.
      - the water the reeds shut in (with the land: between them and the shore, or gaps between them under ~350 m; not
        what land alone shuts in, a harbour's canal) at most LAGOON deep (the owner, 19.11.2026, Haademeeste: "it is big
        lagoon ankle deep usually what makes it flat water there"; 20.11.2026: "model the lagoon depth at Haademeeste"):
        the depth tiles' ~100 m pixels gave its 150-250 m 2-4 m. Reeds grow only in shallow water, so everywhere.
    A river mouth's spot (river: metres, bars.json "river"; the owner, 20.11.2026, Lielupe and Gauja: "kiting inside in
    river behind sand", then "Yes, do A, river first"): its water joined to the sea that far up the river made sea too,
    a channel's depth by the distance to its banks. Elsewhere only ~400 m: Liepaja's lake meets the sea by a canal.
    Breakwaters are marked after, so a pier WorldCover sees as water stays land. The reeds' outlines (for the map: the
    owner, 19.11.2026: "Can we draw reed on map too?", "Yes, build A") from its points round the reed cells."""
    import rasterio  # (lazily: only these jobs install it)
    from scipy.ndimage import binary_closing, binary_dilation, distance_transform_edt
    from rasterio.windows import from_bounds

    lat_of = lambda my: np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * my))))  # noqa: E731
    lons = (box[0] + (np.arange(W * sub) + 0.5) / (W * sub) * box[2]) * 360 - 180
    lats = lat_of(box[1] + (np.arange(H * sub) + 0.5) / (H * sub) * box[3])
    wc = np.zeros((H * sub, W * sub), np.uint8)
    for la in range(int(math.floor(lats.min() / 3) * 3), int(math.floor(lats.max() / 3) * 3) + 1, 3):
        for lo in range(int(math.floor(lons.min() / 3) * 3), int(math.floor(lons.max() / 3) * 3) + 1, 3):
            url = WORLDCOVER.format(f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}")
            # (No tile over open ocean.)
            if _get(url, (0, 16)) is None:
                continue
            rows, cols = np.nonzero((lats >= la) & (lats < la + 3))[0], np.nonzero((lons >= lo) & (lons < lo + 3))[0]
            if not len(rows) or not len(cols):
                continue
            with rasterio.Env(GDAL_HTTP_MAX_RETRY=4, GDAL_HTTP_RETRY_DELAY=5), rasterio.open(url) as f:
                w = from_bounds(lons[cols].min(), lats[rows].min(), lons[cols].max(), lats[rows].max(), f.transform)
                w = w.round_offsets().round_lengths()
                a, t = f.read(1, window=w, boundless=True, fill_value=0), f.window_transform(w)
            py = np.clip(((lats[rows] - t.f) / t.e).astype(int), 0, a.shape[0] - 1)
            px = np.clip(((lons[cols] - t.c) / t.a).astype(int), 0, a.shape[1] - 1)
            wc[np.ix_(rows, cols)] = a[np.ix_(py, px)]
    share = lambda m: m.reshape(H, sub, W, sub).mean(axis=(1, 3))  # noqa: E731
    water, green = share(wc == 80) > 0.5, share(np.isin(wc, GREEN)) > 0.5
    # (Within ~400 m of our coast's sea: its shore's errors, not a lake inland, Liepaja's 2 km^2.)
    cell = box[2] * EARTH * math.cos(math.radians(float(lats.mean()))) / W
    new = (depth == -1) & water & (distance_transform_edt(depth < 0) <= 12)
    if river:
        # (Up the water from the sea, river metres along it: the Lielupe runs behind Jurmala's dunes within 2.5 km of the
        # sea for 30 km, and by the straight distance 4 km^2 of it opened; a lake or pond near it not at all.)
        reach, wet = depth >= 0, ((depth == -1) & water) | (depth >= 0)
        for _ in range(round(river / cell)):
            grown = binary_dilation(reach) & wet
            if (grown == reach).all():
                break
            reach = grown
        new |= reach & (depth == -1)
    if new.any():
        d = distance_transform_edt(water) * cell
        depth[new] = np.minimum(0.2 + 0.12 * d[new] ** (2 / 3), 200.0)
    depth[(depth >= 0) & green] = -2
    if (depth == -2).any():
        r = max(1, round(170 / cell))
        y, x = np.ogrid[-r:r + 1, -r:r + 1]
        disk = x * x + y * y <= r * r
        land = depth == -1
        shut = binary_closing(land | (depth == -2), disk) & ~binary_closing(land, disk) & (depth >= 0)
        depth[shut] = np.minimum(depth[shut], LAGOON)
    if outlines is not None and (depth == -2).any():
        outlines += _outline(np.isin(wc, GREEN), depth == -2, box, sub)
    return new


def _outline(fine, reeds, box, sub):
    """The reeds' outlines ([[ring, ...], ...], rings of [lon, lat]): WorldCover's plant points (sub x sub a cell) in and
    next to the reed cells, traced and rounded off (Chaikin's corners, twice: its 11 m steps showed zoomed in)."""
    from rasterio.features import shapes
    from scipy.ndimage import binary_dilation

    near = np.kron(binary_dilation(reeds), np.ones((sub, sub), bool))
    mask = fine & near
    H, W = mask.shape

    def smooth(ring):
        p = np.asarray(ring, float)[:-1]
        for _ in range(2):
            q = np.roll(p, -1, axis=0)
            p = np.stack([0.75 * p + 0.25 * q, 0.25 * p + 0.75 * q], 1).reshape(-1, 2)
        lon = (box[0] + p[:, 0] / W * box[2]) * 360 - 180
        lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (box[1] + p[:, 1] / H * box[3])))))
        out = [[round(float(a), 5), round(float(b), 5)] for a, b in zip(lon, lat)]
        return out + out[:1]

    polys = []
    for geom, _ in shapes(mask.astype(np.uint8), mask=mask):
        rings = geom["coordinates"]
        # (Specks of a few points left out.)
        if len(rings[0]) >= 6:
            polys.append([smooth(r) for r in rings if len(r) >= 6])
    return polys


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
    print(f"{cells}x{cells} cells of {side / cells:.0f} m: sea {sea.mean() * 100:.0f} %, depth max {d[sea].max():.1f} m, mean {d[sea].mean():.1f} m, reeds {(d == -2).sum()} cells")
