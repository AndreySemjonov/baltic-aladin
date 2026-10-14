"""The Mix as tiles (the app's docs/MIX_TILES_PLAN.md, M1; the owner, 14.10.2026): the app's default
wind map, the Baltic box's blend (blend_map.py), made over Europe from the wind tiles already
published, keeping what makes it the best at the spots:

- the models' weights of the point forecast's backtests (HARM-DK 0.226, MET Nordic 0.181, HARM-FI
  0.181, ICON-EU 0.068, ECMWF 0.034, GFS 0.023); the regional models beyond the Baltic (AROME,
  ICON-D2, ICON-CH1, HARM-NL, ICON-2I) with HARM-DK's until a backtest says otherwise, ALADIN with
  half of it (its edge fades on the tiles);
- each model's land pulled back to the coastline (as maplib.coast_fill: the sea near the shore
  takes the model's own sea wind from beyond its land's reach), the land from our coastline tiles
  (tiles/coast, Natural Earth's shore);
- each model counting by its tiles' own weight (alpha: fading over its area's last 30-150 km);
- speed and gust as weighted means, the direction as a weighted vector mean (weight x speed, calm
  counted as 1 m/s); the 3-hourly hours interpolated (not over a gap of more than 6 hours).

Zooms 4-6 over ICON-EU's area and MET Nordic's north, zoom 7 where MET Nordic's 1 km tiles are;
hourly from now for a day, then every 3 hours while a regional model reaches (about 60 hours; the
app's ICON-EU and ECMWF layers take over after). Its tiles fade (alpha) where only ECMWF and GFS are. Each hour lists its models;
the data checks (R3) as the other sets; published as tiles/mix.

    python mix_tiles.py [--force] [--local DIR] [--hours N] [--box S,N,W,E]
"""
import io
import json
import math
import struct
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
from PIL import Image
from scipy import ndimage

import quality
import tiler

FILES = "https://files.meteogust.com/tiles/"
AGENT = "baltic-aladin mix_tiles (+https://github.com/AndreySemjonov/baltic-aladin)"
# Set, name, weight, how far the model's land reaches over the sea (km, about 1.5 of its cells).
MODELS = [
    ("metnordic-1km", "MET Nordic", 0.181, 1.5),
    ("dini", "HARM-DK", 0.226, 3.0),
    ("harm-fi", "HARM-FI", 0.181, 3.75),
    ("arome", "AROME", 0.226, 2.0),
    ("icon-d2", "ICON-D2", 0.226, 3.3),
    ("icon-ch1", "ICON-CH1", 0.226, 1.5),
    ("harm-nl", "HARM-NL", 0.226, 3.0),
    ("icon-2i", "ICON-2I", 0.226, 3.3),
    # Out of the Baltic box's Mix for its edge; the tiles fade it out: half the class's weight
    # until a backtest says otherwise (the owner, 14.10.2026).
    ("aladin", "ALADIN", 0.113, 3.5),
    ("icon-eu", "ICON-EU", 0.068, 10.0),
    ("ecmwf", "ECMWF", 0.034, 25.0),
    ("gfs", "GFS", 0.023, 25.0),
]
GLOBAL = {"ecmwf", "gfs"}
REGIONAL = {m[0] for m in MODELS} - GLOBAL - {"icon-eu"}
BOX = (29.5, 74.0, -23.5, 62.5)
ZOOMS = range(4, 8)
SIZE = 256
# Pixels round a tile seen for the coast (ECMWF's land reaches 25 km: about 18 zoom-6 pixels here).
MARGIN = 40
MAX_GAP = 6 * 3600
CREDIT = ("MET Nordic, MET Norway; HARMONIE DINI, DMI; HARMONIE, FMI; ALADIN, ČHMÚ; AROME, Météo-France (Etalab 2.0); "
          "ICON-D2 and ICON-EU, DWD; ICON-CH1, MeteoSwiss; HARMONIE-AROME, KNMI; ICON-2I, ItaliaMeteo; "
          "ECMWF open data (CC BY 4.0); GFS, NOAA")


def get(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=120) as reply:
                return reply.read()
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(5)


class Pack:
    """A published pack (MGT1): its tiles decoded when asked, kept."""

    def __init__(self, data):
        n = struct.unpack("<I", data[4:8])[0]
        head = json.loads(data[8:8 + n])
        self.tiles, self.start, self.data = head["tiles"], 8 + n, data
        self.sizes = {int(z): s for z, s in head["sizes"].items()}
        self.top = max(int(k.split("/")[0]) for k in self.tiles) if self.tiles else 0
        self.cache = {}

    def tile(self, z, x, y):
        key = (z, x, y)
        if key not in self.cache:
            r = self.tiles.get(f"{z}/{x}/{y}")
            self.cache[key] = None if r is None else np.asarray(
                Image.open(io.BytesIO(self.data[self.start + r[0]:self.start + r[0] + r[1]])).convert("RGBA"), dtype=np.float32)
        return self.cache[key]


def sample(pack, z, px0, py0, w, h):
    """The model's wind over a block of zoom-z pixels (px0, py0: its top left, the world's pixels at
    256 a tile): east, north, gust (m/s) and its weight, bilinear from its finest zoom up to z
    (weighted, so its edge's empty pixels don't pull the wind to calm). Only the source pixels the
    block needs are put together."""
    zm = min(z, pack.top)
    size = pack.sizes.get(zm, SIZE)
    scale = 2 ** (z - zm)
    n = 2 ** zm
    # The block's pixels on the model's zoom, and the source window round them (one more each way).
    xs = (px0 + np.arange(w) + 0.5) / SIZE / scale * size - 0.5
    ys = (py0 + np.arange(h) + 0.5) / SIZE / scale * size - 0.5
    c0, c1 = int(math.floor(xs[0])) - 1, int(math.floor(xs[-1])) + 2
    r0, r1 = int(math.floor(ys[0])) - 1, int(math.floor(ys[-1])) + 2
    window = np.zeros((r1 - r0 + 1, c1 - c0 + 1, 4), np.float32)
    window[..., :2] = 128
    any_tile = False
    for ty in range(r0 // size, r1 // size + 1):
        if not (0 <= ty < n):
            continue
        for tx in range(c0 // size, c1 // size + 1):
            t = pack.tile(zm, tx % n, ty)
            if t is None or t.shape[0] != size:
                continue
            # The tile's part of the window.
            a0, a1 = max(r0, ty * size), min(r1, ty * size + size - 1)
            b0, b1 = max(c0, tx * size), min(c1, tx * size + size - 1)
            window[a0 - r0:a1 - r0 + 1, b0 - c0:b1 - c0 + 1] = t[a0 - ty * size:a1 - ty * size + 1, b0 - tx * size:b1 - tx * size + 1]
            any_tile = True
    if not any_tile:
        return None
    a = window[..., 3] / 255
    if scale == 1 and size == SIZE:
        # The model's own zoom: its pixels as they are.
        part, weight = window[1:1 + h, 1:1 + w], a[1:1 + h, 1:1 + w]
        if weight.max() <= 0.004:
            return None
        return ((part[..., 0] - 128) / 4, (part[..., 1] - 128) / 4, part[..., 2] / 5, weight.astype(np.float32))
    gy, gx = np.meshgrid(ys - r0, xs - c0, indexing="ij")
    coords = [gy, gx]

    def at(values):
        return ndimage.map_coordinates(values, coords, order=1, mode="nearest").astype(np.float32)
    weight = at(a)
    if weight.max() <= 0.004:
        return None
    safe = np.maximum(weight, 1e-6)
    east = at((window[..., 0] - 128) / 4 * a) / safe
    north = at((window[..., 1] - 128) / 4 * a) / safe
    gust = at(window[..., 2] / 5 * a) / safe
    return east, north, gust, np.clip(weight, 0, 1)


class Coast:
    """The land (1) and sea (0) of a block of pixels from our coastline tiles (zooms 5-8: 127.5 on
    the shore, more inland); zoom 4 from zoom 5 two by two."""

    def __init__(self):
        index = json.loads(get(FILES + "coast/index.json"))
        self.packs, self.land = set(index["packs"]), set(index.get("land", []))
        self.cache = {}
        self.blocks = {}

    def pack(self, x5, y5):
        name = f"{x5}-{y5}"
        if name not in self.cache:
            self.cache[name] = Pack(get(FILES + f"coast/5-{name}.pack")) if name in self.packs else None
        return self.cache[name]

    def cached(self, z, px0, py0, w, h):
        """A block, kept (the same tiles every hour)."""
        key = (z, px0, py0, w, h)
        if key not in self.blocks:
            self.blocks[key] = self.block(z, px0, py0, w, h)
        return self.blocks[key]

    def block(self, z, px0, py0, w, h):
        if z < 5:
            fine = self.block(5, px0 * 2, py0 * 2, w * 2, h * 2)
            return fine.reshape(h, 2, w, 2).mean(axis=(1, 3))
        out = np.zeros((h, w), np.float32)
        n = 2 ** z
        for ty in range(py0 // SIZE, (py0 + h - 1) // SIZE + 1):
            for tx in range(px0 // SIZE, (px0 + w - 1) // SIZE + 1):
                if not (0 <= ty < n):
                    continue
                txw = tx % n
                f = 2 ** (z - 5)
                x5, y5 = txw // f, ty // f
                if f"{x5}-{y5}" in self.packs:
                    t = self.pack(x5, y5).tile(z, txw, ty)
                    land = (t[..., 0] > 127.5).astype(np.float32) if t is not None else np.zeros((SIZE, SIZE), np.float32)
                else:
                    land = np.full((SIZE, SIZE), 1.0 if f"{x5}-{y5}" in self.land else 0.0, np.float32)
                # The tile's part of the block.
                x0, y0 = tx * SIZE - px0, ty * SIZE - py0
                sx0, sy0 = max(0, -x0), max(0, -y0)
                dx0, dy0 = max(0, x0), max(0, y0)
                cw, ch = min(SIZE - sx0, w - dx0), min(SIZE - sy0, h - dy0)
                if cw > 0 and ch > 0:
                    out[dy0:dy0 + ch, dx0:dx0 + cw] = land[sy0:sy0 + ch, sx0:sx0 + cw]
        return out


_fills = {}


def coast_fill(arrays, land, buffer_px, valid, key):
    """As maplib.coast_fill: sea pixels within `buffer_px` of the land take the value of the nearest
    sea pixel beyond it with the model's data (not from further than twice the buffer)."""
    if buffer_px < 0.75:
        return
    cache = (key, round(buffer_px, 1), hash(valid.tobytes()))
    if cache not in _fills:
        is_land = land >= 0.5
        sea = ~is_land
        from_land = ndimage.distance_transform_edt(~is_land)
        source = sea & (from_land > buffer_px + 0.5) & valid
        if not source.any():
            _fills[cache] = None
        else:
            distance, (ri, ci) = ndimage.distance_transform_edt(~source, return_indices=True)
            target = sea & ~source & (distance <= 2 * buffer_px + 1) & valid
            _fills[cache] = (target, ri[target], ci[target])
        if len(_fills) > 20000:
            _fills.clear()
    found = _fills[cache]
    if found is not None:
        target, ri, ci = found
        for values in arrays:
            values[target] = values[ri, ci]


class Model:
    """One set's published hours: its packs read when needed, the wind at any time between them."""

    def __init__(self, name, label, weight, coast_km):
        self.name, self.label, self.weight, self.coast_km = name, label, weight, coast_km
        index = json.loads(get(FILES + f"{name}/index.json"))
        self.files = {f["time"]: f["file"] for f in index["frames"] if not f["file"].endswith(("-temp.pack", "-rain.pack"))}
        self.times = sorted(self.files)
        self.packs = {}

    def pack(self, t):
        if t not in self.packs:
            self.packs[t] = Pack(get(FILES + f"{self.name}/{self.files[t]}"))
        return self.packs[t]

    def around(self, t):
        """The hours to read for time t and how far between them: [(time, share)]; [] when none."""
        if t in self.files:
            return [(t, 1.0)]
        later = next((x for x in self.times if x > t), None)
        earlier = next((x for x in reversed(self.times) if x < t), None)
        if later is None or earlier is None or later - earlier > MAX_GAP:
            return []
        share = (t - earlier) / (later - earlier)
        return [(earlier, 1 - share), (later, share)]

    def forget(self, before):
        for t in [t for t in self.packs if t < before]:
            del self.packs[t]


def model_block(model, hours, z, px0, py0, w, h):
    """The model's wind over a block at a time (`hours` from Model.around), or None."""
    total = None
    for t, share in hours:
        got = sample(model.pack(t), z, px0, py0, w, h)
        if got is None:
            return None
        total = [g * share for g in got] if total is None else [a + g * share for a, g in zip(total, got)]
    return total


def tile_km(z, y):
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / 2 ** z))))
    return 40075.016 * math.cos(math.radians(lat)) / (2 ** z * SIZE)


# Tuned weights by place (the owner, 14.10.2026: weights noted for places round Europe, used without
# showing them): mix_weights.json, {"radius_km": 250, "points": [{"name", "lat", "lon", "radius_km"?,
# "weights": {model name: weight}}]}. Near a point its weights count, between points they blend
# smoothly (a Gaussian of its radius), far from all of them the weights above; a model a point
# doesn't name keeps the one above there.
WEIGHTS_FILE = "mix_weights.json"
DEFAULT_SHARE = 0.05
_points = None
_tile_weights = {}


def weight_points():
    global _points
    if _points is None:
        try:
            with open(WEIGHTS_FILE, encoding="utf-8") as file:
                data = json.load(file)
            radius = float(data.get("radius_km", 250))
            _points = [(float(p["lat"]), float(p["lon"]), float(p.get("radius_km", radius)), p["weights"])
                       for p in data.get("points", []) if p.get("weights")]
        except (OSError, ValueError, KeyError):
            _points = []
        if _points:
            print(f"Tuned weights: {len(_points)} points", flush=True)
    return _points


def tile_weights(models, z, px0, py0, w, h):
    """Each model's weight over a block of pixels (an array; its number when no point is near)."""
    points = weight_points()
    key = (z, px0, py0, w, h)
    if key in _tile_weights:
        return _tile_weights[key]
    plain = {m.label: m.weight for m in models}
    if not points:
        return plain
    n = 2 ** z * SIZE
    lon = (px0 + np.arange(w) + 0.5) / n * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (py0 + np.arange(h) + 0.5) / n))))
    lat, lon = np.meshgrid(lat, lon, indexing="ij")
    strengths = []
    for plat, plon, radius, _ in points:
        dy = (lat - plat) * 111.2
        dx = (lon - plon) * 111.2 * np.cos(np.radians(plat))
        strengths.append(np.exp(-(dx * dx + dy * dy) / (radius * radius)).astype(np.float32))
    if max(float(s.max()) for s in strengths) < 0.01:
        _tile_weights[key] = plain
        return plain
    total = sum(strengths) + DEFAULT_SHARE
    out = {}
    for m in models:
        mixed = DEFAULT_SHARE * m.weight + sum(s * float(p[3].get(m.label, m.weight)) for s, p in zip(strengths, points))
        out[m.label] = mixed / total
    _tile_weights[key] = out
    if len(_tile_weights) > 5000:
        _tile_weights.clear()
    return out


def mix_tile(models, hours, coast, z, x, y):
    """One tile of the Mix: its RGBA (wind_rgb and the weight), the models in it; None if empty."""
    px0, py0 = x * SIZE - MARGIN, y * SIZE - MARGIN
    w = h = SIZE + 2 * MARGIN
    land = None
    km = tile_km(z, y)
    weights = tile_weights(models, z, px0, py0, w, h)
    total = np.zeros((h, w), np.float32)
    speed = np.zeros((h, w), np.float32)
    gust = np.zeros((h, w), np.float32)
    east = np.zeros((h, w), np.float32)
    north = np.zeros((h, w), np.float32)
    cover = np.zeros((h, w), np.float32)
    used = []
    for model in models:
        if not hours.get(model.name):
            continue
        got = model_block(model, hours[model.name], z, px0, py0, w, h)
        if got is None:
            continue
        u, v, g, a = got
        if land is None:
            land = coast.cached(z, px0, py0, w, h)
        s = np.hypot(u, v)
        # The model's land pulled back to the coast (u, v, speed, gust as the blend does).
        arrays = [u, v, s, g]
        coast_fill(arrays, land, model.coast_km / km, a > 0.5, (model.name, z, x, y))
        u, v, s, g = arrays
        wgt = weights[model.label] * a
        unit = np.maximum(np.hypot(u, v), 1e-6)
        pull = wgt * np.maximum(s, 1.0)
        east += pull * u / unit
        north += pull * v / unit
        total += wgt
        speed += wgt * s
        gust += wgt * g
        if model.name not in GLOBAL:
            cover = np.maximum(cover, a)
        if (a[MARGIN:-MARGIN, MARGIN:-MARGIN] > 0.02).any():
            used.append(model.label)
    c = slice(MARGIN, MARGIN + SIZE)
    total, speed, gust, east, north, cover = (arr[c, c] for arr in (total, speed, gust, east, north, cover))
    alpha = np.where(total > 0, cover, 0)
    if alpha.max() < 0.004:
        return None
    safe = np.where(total > 0, total, 1)
    s = speed / safe
    unit = np.maximum(np.hypot(east, north), 1e-6)
    ue, un = s * east / unit, s * north / unit
    g = np.maximum(gust / safe, s)
    rgb = tiler.wind_rgb(ue, un, g)
    rgba = np.concatenate([rgb, np.rint(np.clip(alpha, 0, 1) * 255)[..., None]], axis=-1).astype(np.uint8)
    return rgba, used, (ue[alpha > 0.5], un[alpha > 0.5], g[alpha > 0.5])


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    box = tuple(float(v) for v in args[args.index("--box") + 1].split(",")) if "--box" in args else BOX
    started = time.time()
    models = []
    for name, label, weight, km in MODELS:
        try:
            models.append(Model(name, label, weight, km))
        except Exception as error:
            print(f"{label}: no index ({error})", flush=True)
    regional = [m for m in models if m.name in REGIONAL]
    if not regional:
        raise SystemExit("No regional model to mix")
    now = int(time.time()) // 3600 * 3600
    last = max(m.times[-1] for m in regional if m.times)
    # Hourly for the first day, then every 3 hours (as MET Nordic's 1 km tiles; the app goes
    # smoothly between them): about 0.65 GB a run.
    hours = [t for t in range(now, last + 1, 3600) if t - now < 24 * 3600 or (t - now) % (3 * 3600) == 0]
    if limit:
        hours = hours[:limit]
    run_time = datetime.fromtimestamp(now, timezone.utc)
    out = tiler.Run("mix", run_time, ZOOMS, "Mix", CREDIT, "--force" in args, local)
    if not out.wanted:
        print(f"Mix {run_time:%Y%m%dT%HZ} already published")
        return
    coast = Coast()
    # The tiles: zooms 4-6 over the box, zoom 7 where MET Nordic's 1 km tiles are.
    keys = tiler.tiles_in_box(box, range(4, 7))
    fine = next((m for m in models if m.name == "metnordic-1km" and m.times), None)
    if fine:
        top = fine.pack(fine.times[0])
        south, north, west, east = box
        for k in top.tiles:
            z, x, y = (int(v) for v in k.split("/"))
            if z == 7:
                lon = (x + 0.5) / 128 * 360 - 180
                lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / 128))))
                if south <= lat <= north and west <= lon <= east:
                    keys.append((7, x, y))
    print(f"Mix {run_time:%Y%m%dT%HZ}: {len(keys)} tiles, {len(hours)} hours, models: "
          + ", ".join(m.label for m in models), flush=True)
    for t in hours:
        when = {m.name: m.around(t) for m in models}
        # The packs of this hour read side by side first.
        needed = [(m, ht) for m in models for ht, _ in when[m.name] if ht not in m.packs]
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda job: job[0].pack(job[1]), needed))
        tiles, used, checks = {}, set(), ([], [], [])
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda key: (key, mix_tile(models, when, coast, *key)), keys))
        for key, result in results:
            if result is None:
                continue
            rgba, names, (ue, un, g) = result
            tiles[key] = tiler.world_tiles.png(rgba, "RGBA")
            used.update(names)
            for i, arr in enumerate((ue, un, g)):
                if arr.size:
                    checks[i].append(arr[::7])
        for m in models:
            m.forget(t - MAX_GAP)
        if not tiles:
            continue
        east, north, gust = (np.concatenate(c) if c else np.zeros(0, np.float32) for c in checks)
        if not out.checked("Mix", t, quality.wind(east, north, gust)):
            continue
        out.put(t, tiler.world_tiles.encode(tiles, {z: SIZE for z in ZOOMS}))
        out.frames[t]["models"] = [m.label for m in models if m.label in used]
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%d %H:%M}: {len(tiles)} tiles, "
              f"{', '.join(out.frames[t]['models'])}, {time.time() - started:.0f} s", flush=True)
    out.finish(weights={m.label: m.weight for m in models})
    print(f"Mix done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
