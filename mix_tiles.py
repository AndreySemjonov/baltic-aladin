"""The Mix as tiles (the app's docs/MIX_TILES_PLAN.md, M1; the owner, 14.10.2026): the app's default
wind map, the Baltic box's blend (blend_map.py), made over Europe from the wind tiles already
published, keeping what makes it the best at the spots:

- the models' weights by place, one set for the spots, the map and new spots: near the app's catalog
  spots their own (Windguru's for each spot), blended smoothly between them, far from all of them by
  the WG formula (1 / the model's resolution in km, as the app's SpotWeights);
- a coarse model's land (ICON-EU, ECMWF, GFS) pulled back to the coastline: its sea near the shore
  towards its own sea wind beyond its land's reach, all of it at the shore, less out to the reach
  (the fine models resolve the coast themselves), the land from our coastline tiles (tiles/coast);
- each model counting by its tiles' own weight (alpha: fading over its area's last 30-150 km);
- speed and gust as weighted means, the direction as a weighted vector mean (weight x speed, calm
  counted as 1 m/s); the 3-hourly hours interpolated (not over a gap of more than 6 hours).

Zooms 4-6 over ICON-EU's area, MET Nordic's north and DINI's west, zoom 7 where the 1 km models' tiles are (MET Nordic, AROME, ICON-CH1);
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
    # The default weights (far from every weight point): the WG formula, as the app's SpotWeights
    # (the owner, 17.10.2026: one rule everywhere): 1 / the model's resolution in km (MET Nordic as
    # 2.5 km, its real resolution); its run freshness left out. Near the catalog's spots, their own
    # weights (weight_points).
    ("metnordic-1km", "MET Nordic", 1 / 2.5, 1.5),
    ("dini", "HARM-DK", 1 / 2.0, 3.0),
    ("harm-fi", "HARM-FI", 1 / 2.5, 3.75),
    ("arome", "AROME", 1 / 1.5, 2.0),
    ("icon-d2", "ICON-D2", 1 / 2.2, 3.3),
    ("icon-ch1", "ICON-CH1", 1 / 1.0, 1.5),
    ("harm-nl", "HARM-NL", 1 / 2.0, 3.0),
    ("icon-2i", "ICON-2I", 1 / 2.2, 3.3),
    ("aladin", "ALADIN", 1 / 2.3, 3.5),
    # The UK Met Office's UKV (ukmo_tiles.py, 18.10.2026).
    ("ukmo", "UKV", 1 / 2.0, 3.0),
    # Europe's last models from Open-Meteo's open data (bucket_tiles.py, 18.10.2026).
    ("icon-ch2", "ICON-CH2", 1 / 2.1, 3.2),
    ("arome25", "AROME-FR", 1 / 2.5, 3.75),
    ("knmi-eu", "HARM-EU", 1 / 5.0, 8.0),
    ("icon-eu", "ICON-EU", 1 / 7.0, 10.0),
    ("ecmwf", "ECMWF", 1 / 9.0, 25.0),
    ("gfs", "GFS", 1 / 13.0, 25.0),
]
# The defaults on the app's scale: the formula over all its 20 forecast models (their grids in the
# app's ForecastSpots.json), normalized to 1 as a spot's weights are, so a model a spot doesn't name
# counts beside them as in the app.
APP_GRIDS = {"dmi_harmonie_arome_europe": 2, "metno_nordic": 2.5, "aladin": 2.3, "fmi": 2.5, "meteofrance_arome_france_hd": 1.5,
             "knmi_harmonie_arome_netherlands": 2, "ukmo_uk_deterministic_2km": 2, "icon_d2": 2.2, "meteoswiss_icon_ch1": 1,
             "meteoswiss_icon_ch2": 2.1, "italia_meteo_arpae_icon_2i": 2.2, "meteofrance_arome_france": 2.5, "ncep_hrrr_conus": 3,
             "gem_hrdps_continental": 2.5, "knmi_harmonie_arome_europe": 5, "icon_eu": 7, "ecmwf_ifs": 9, "gfs_global": 13,
             "icon_global": 13 / 0.9, "gem_global": 15 / 0.7}
KIND_TOTAL = sum(1 / km for km in APP_GRIDS.values())
MODELS = [(name, label, weight / KIND_TOTAL, km) for name, label, weight, km in MODELS]
GLOBAL = {"ecmwf", "gfs"}

# Fresher runs count more (the owner, 15.10.2026, after reading how the WG mix weights its models;
# the same as the app's spot forecasts, ForecastEngine.freshness): each model's weight times
# 100 x (hours its run is older than the newest model's + 4)^(-s/2) - 3 x s x hours (at least 0),
# scaled so they stay about 1 (2 / (largest + smallest), the largest at most 1.5). 0: off. Set from
# the backtest (the app's backend/lab/freshness.py).
FRESHNESS = 0.0
FRESH = {}


def freshness(runs, sensitivity=FRESHNESS):
    """Each model's factor from its run's start (Unix s): {label: factor}; {} when off or none."""
    if sensitivity <= 0 or not runs:
        return {}
    newest = max(runs.values())
    raw = {k: max(100 * ((newest - r) / 3600 + 4) ** (-sensitivity / 2) - 3 * sensitivity * (newest - r) / 3600, 0)
           for k, r in runs.items()}
    top, low = max(raw.values()), min(raw.values())
    if top + low <= 0:
        return {}
    scale = 2 / (top + low)
    if top * scale > 1.5:
        scale = 1.5 / top
    return {k: v * scale for k, v in raw.items()}


def run_unix(text):
    """An index's run ("2026-10-15T09:00Z") as Unix seconds; None when it has none."""
    try:
        return int(datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None
REGIONAL = {m[0] for m in MODELS} - GLOBAL - {"icon-eu"}
# ICON-EU's area, MET Nordic's north and DINI's west (Greenland's coast, Iceland: 14.10.2026).
BOX = (29.5, 74.0, -45.0, 62.5)
ZOOMS = range(4, 8)
SIZE = 256
# Pixels round a tile seen for the coast (ECMWF's land reaches 25 km: about 18 zoom-6 pixels here).
MARGIN = 40
MAX_GAP = 6 * 3600
CREDIT = ("MET Nordic, MET Norway; HARMONIE DINI, DMI; HARMONIE, FMI; ALADIN, ČHMÚ; AROME, Météo-France (Etalab 2.0); "
          "ICON-D2 and ICON-EU, DWD; ICON-CH1, MeteoSwiss; HARMONIE-AROME, KNMI; ICON-2I, ItaliaMeteo; "
          "ECMWF open data (CC BY 4.0); GFS, NOAA; UKV, Met Office; ICON-CH2, MeteoSwiss; HARMONIE-AROME Europe, KNMI")


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
        # The packs made again go in a folder of their own (v2, ...: the world's coasts since v4).
        self.folder = (index["folder"] + "/") if index.get("folder") else ""
        self.cache = {}
        self.blocks = {}

    def pack(self, x5, y5):
        name = f"{x5}-{y5}"
        if name not in self.cache:
            self.cache[name] = Pack(get(FILES + f"coast/{self.folder}5-{name}.pack")) if name in self.packs else None
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


# Only a coarse model's land is pulled back (its cells straddle the shore); the fine ones (1-3 km)
# resolve the coast themselves, the wind picking up over the water as it does (the owner,
# 14.10.2026: replaced near the shore, their rise became a step at a few km, jagged).
COARSE_KM = 5.0


def coast_fill(arrays, land, buffer_px, valid, key):
    """A coarse model's sea near the land towards its sea beyond: sea pixels within `buffer_px` of
    the land take the nearest sea value beyond it, all of it at the shore and less out to the
    buffer (no step), only from pixels with the model's data."""
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
            share = np.clip(1 - (from_land[target] - 0.5) / max(buffer_px, 1), 0, 1).astype(np.float32)
            _fills[cache] = (target, ri[target], ci[target], share)
        if len(_fills) > 20000:
            _fills.clear()
    found = _fills[cache]
    if found is not None:
        target, ri, ci, share = found
        for values in arrays:
            values[target] = values[target] * (1 - share) + values[ri, ci] * share


class Model:
    """One set's published hours: its packs read when needed, the wind at any time between them."""

    def __init__(self, name, label, weight, coast_km):
        self.name, self.label, self.weight, self.coast_km = name, label, weight, coast_km
        index = json.loads(get(FILES + f"{name}/index.json"))
        self.run = run_unix(index.get("run"))
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


# Weights by place (the owner, 14.10.2026: one set of weights for the spots, the map and new spots):
# the app's catalog spots with their own weights (catalog_points), and any points of
# mix_weights.json ({"radius_km": 250, "points": [{"name", "lat", "lon", "radius_km"?, "weights":
# {model name: weight}}]}). Near a point its weights count, between points they blend
# smoothly (a Gaussian of its radius), far from all of them the weights above; a model a point
# doesn't name keeps the one above there.
WEIGHTS_FILE = "mix_weights.json"
DEFAULT_SHARE = 0.05
_points = None
_tile_weights = {}


# The spot forecasts' model keys (Open-Meteo's, the app's catalog) and the Mix's models; the keys
# without tiles (UKV, ICON 13 km, GDPS, HARMONIE 5 km, ...) leave their share to the others.
CATALOG = "https://data.meteogust.com/spots/catalog"
KEYS = {"dmi_harmonie_arome_europe": "HARM-DK", "metno_nordic": "MET Nordic", "fmi": "HARM-FI", "aladin": "ALADIN",
        "meteofrance_arome_france_hd": "AROME", "meteofrance_arome_france": "AROME-FR", "icon_d2": "ICON-D2",
        "meteoswiss_icon_ch1": "ICON-CH1", "meteoswiss_icon_ch2": "ICON-CH2", "italia_meteo_arpae_icon_2i": "ICON-2I",
        "knmi_harmonie_arome_netherlands": "HARM-NL", "icon_eu": "ICON-EU", "ecmwf_ifs": "ECMWF", "gfs_global": "GFS",
        # The world mix's (mix_world.py; not in this one).
        "icon_global": "ICON", "gem_global": "GDPS", "ukmo_uk_deterministic_2km": "UKV", "knmi_harmonie_arome_europe": "HARM-EU"}
# Copied weights (a rider's spot took its nearest spot's) aren't a place's own: left out.
COPIED = ("nearest built-in spot", "default", "map's weights")


def catalog_points(radius):
    """The weight points from the app's spot catalog (the owner, 14.10.2026: one set of weights for the
    spots, the map and new spots): each spot with its own weights (hidden ones too, a weights-only
    point is a hidden spot), its models' keys turned into the Mix's names (a name two keys share
    takes their sum)."""
    try:
        spots = json.loads(get(CATALOG))["spots"]
    except Exception as error:
        print(f"No catalog ({error}): its weights left out", flush=True)
        return []
    out = []
    for entry in spots:
        spot = entry.get("spot") or {}
        source = str(spot.get("weightsSource", ""))
        if not spot.get("weights") or any(c in source for c in COPIED):
            continue
        named = {}
        for key, weight in spot["weights"].items():
            if key in KEYS:
                named[KEYS[key]] = named.get(KEYS[key], 0) + float(weight)
        if named:
            out.append((float(spot["lat"]), float(spot["lon"]), radius, named))
    return out


def weight_points():
    """The catalog's spots, and the points of mix_weights.json (if any)."""
    global _points
    if _points is None:
        radius = 250.0
        extra = []
        try:
            with open(WEIGHTS_FILE, encoding="utf-8") as file:
                data = json.load(file)
            radius = float(data.get("radius_km", radius))
            extra = [(float(p["lat"]), float(p["lon"]), float(p.get("radius_km", radius)), p["weights"])
                     for p in data.get("points", []) if p.get("weights")]
        except (OSError, ValueError, KeyError):
            pass
        spots = catalog_points(radius)
        _points = spots + extra
        print(f"Weights: {len(spots)} catalog spots, {len(extra)} other points", flush=True)
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
    if FRESH:
        weights = {k: v * FRESH.get(k, 1.0) for k, v in weights.items()}
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
        # A coarse model's land pulled back to the coast (u, v, speed, gust as the blend does).
        if model.coast_km >= COARSE_KM:
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
    # A light smoothing (about a pixel: no stairs where the models' areas or the coast meet).
    total, speed, gust, east, north = (ndimage.gaussian_filter(arr, 0.8) for arr in (total, speed, gust, east, north))
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
    FRESH.update(freshness({m.label: m.run for m in models if m.run}))
    if FRESH:
        print("Freshness: " + ", ".join(f"{k} {v:.2f}" for k, v in sorted(FRESH.items(), key=lambda kv: -kv[1])), flush=True)
    now = int(time.time()) // 3600 * 3600
    last = max(m.times[-1] for m in regional if m.times)
    # Every hour (the owner, 18.10.2026: the models' full resolution; the regional models' tiles are
    # hourly over their whole range): about 1 GB a run.
    hours = list(range(now, last + 1, 3600))
    if limit:
        hours = hours[:limit]
    run_time = datetime.fromtimestamp(now, timezone.utc)
    out = tiler.Run("mix", run_time, ZOOMS, "Mix", CREDIT, "--force" in args, local)
    if not out.wanted:
        print(f"Mix {run_time:%Y%m%dT%HZ} already published")
        return
    coast = Coast()
    # The tiles: zooms 4-6 over the box, zoom 7 where the 1 km models' tiles are (MET Nordic's; AROME's
    # and ICON-CH1's since 18.10.2026: the models' full detail on the map and at a tap).
    keys = tiler.tiles_in_box(box, range(4, 7))
    south, north, west, east = box
    seen = set()
    for fine in (m for m in models if m.name in ("metnordic-1km", "arome", "icon-ch1") and m.times):
        top = fine.pack(fine.times[0])
        for k in top.tiles:
            z, x, y = (int(v) for v in k.split("/"))
            if z == 7 and (x, y) not in seen:
                lon = (x + 0.5) / 128 * 360 - 180
                lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / 128))))
                if south <= lat <= north and west <= lon <= east:
                    keys.append((7, x, y))
                    seen.add((x, y))
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
            # Only the hours this one read stay (each pack keeps its decoded tiles, about 1 MB each:
            # 16 models' six hours ran the runner out of memory at the tenth hour, 18.10.2026).
            m.forget(min((ht for ht, _ in when[m.name]), default=t))
        if not tiles:
            continue
        east, north, gust = (np.concatenate(c) if c else np.zeros(0, np.float32) for c in checks)
        if not out.checked("Mix", t, quality.wind(east, north, gust)):
            continue
        out.put(t, tiler.world_tiles.encode(tiles, {z: SIZE for z in ZOOMS}))
        out.frames[t]["models"] = [m.label for m in models if m.label in used]
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%d %H:%M}: {len(tiles)} tiles, "
              f"{', '.join(out.frames[t]['models'])}, {time.time() - started:.0f} s", flush=True)
    out.finish(weights={m.label: m.weight for m in models}, freshness={k: round(v, 3) for k, v in FRESH.items()})
    print(f"Mix done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up: their exit can crash Python after the work is published
    # ("double free or corruption", 18.10.2026), failing the run and the workflow's next step.
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
