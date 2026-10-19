"""The weather mix (the owner, 18.10.2026: the weather as advanced as the wind, every model we have
blended by place, as the Mix does for the wind), from the weather layers already published:

- the air: each model's moved from its own ground (tiles/heights, its grid's HSURF) to ours (tiles/
  terrain: the pixel's mean height of Copernicus GLO-90) at 6.5 °C a km, as Open-Meteo does, then
  averaged by the models' weights (1 / their resolution in km, as the Mix's formula) and how much
  each counts there (its tiles' alpha); how it feels the same way; the sea-level pressure averaged;
- the cloud cover averaged;
- the rain three ways (averaging alone smears a shower at 14:00 in one model and 16:00 in another
  into drizzle all afternoon): the chance, the models' weighted vote for 0.1 mm or more in the hour
  (rain within NEAR_KM counting half: showers a few km off are nearly right), blended with the
  ensembles' own chance (Open-Meteo's open data: ECMWF's 51 runs, GEFS's 31, GEPS, the UK's global
  and 2 km ensembles, MeteoSwiss's ICON-CH2 ensemble), more of it the further ahead (the owner,
  18.10.2026: a real rain chance anywhere); the typical amount, their weighted median; and their
  mean, for the days' totals.

Two sets, each hour two packs (<t>-temp.pack, <t>-rain.pack) as the other weather sets:

    mix-wx         Europe (mix_tiles.BOX) at zooms 4-6, the regional models' hours (about 2-3 days)
    mix-world-wx   the world at zooms 0-4, hourly to 5 days, 3-hourly to 6, then 6-hourly to 15

    temp: red the air (°C + 40) x 3 + 1 at the pixel's mean height, green how it feels the same
          way, blue the sea-level pressure (hPa - 940) x 2 (0 unknown); alpha 255
    rain: red sqrt(the typical mm an hour) x 50, green the cloud cover x 255, blue the chance % x 2.55,
          alpha 128 + sqrt(the mean mm an hour) x 40 (to 127)

station-history's /weather reads them and moves the air on to the place's own height (zoom 9).

    python weather_mix.py europe|world [--force] [--local DIR] [--hours N]
"""
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy import ndimage

import mix_tiles as mt
import quality
import temp_map
import tiler

FILES = "https://files.meteogust.com/tiles/"
SIZE = 256
MARGIN = 16
MAX_GAP = 6 * 3600
LAPSE = 0.0065
# The air's weights (mix_tile): MET Nordic's boost; the ground's gap that weighs a model e times less (m).
TEMP_BOOST = {"metnordic-1km-wx": 4.0}
HEIGHT_SCALE = 300.0
NEAR_KM = 10
WET = 0.1
# Set, label, resolution (km), its ground height's model (tiles/heights/<model>.pack).
EUROPE = [
    ("metnordic-1km-wx", "MET Nordic", 2.5, "metno_nordic_pp"),
    ("dini-wx", "HARM-DK", 2.0, "dmi_harmonie_arome_europe"),
    ("arome-wx", "AROME", 1.5, "meteofrance_arome_france_hd"),
    ("icon-d2-wx", "ICON-D2", 2.2, "dwd_icon_d2"),
    ("icon-ch1-wx", "ICON-CH1", 1.0, "meteoswiss_icon_ch1"),
    ("harm-nl-wx", "HARM-NL", 2.0, "knmi_harmonie_arome_netherlands"),
    ("icon-2i-wx", "ICON-2I", 2.2, "italia_meteo_arpae_icon_2i"),
    ("ukmo", "UKV", 2.0, "ukmo_uk_deterministic_2km"),
    ("icon-ch2", "ICON-CH2", 2.1, "meteoswiss_icon_ch2"),
    ("arome25", "AROME-FR", 2.5, "meteofrance_arome_france0025"),
    ("knmi-eu", "HARM-EU", 5.0, "knmi_harmonie_arome_europe"),
    ("icon-eu-wx", "ICON-EU", 7.0, "dwd_icon_eu"),
    ("ecmwf", "ECMWF", 9.0, "ecmwf_ifs"),
    ("gfs", "GFS", 13.0, "ncep_gfs025"),
]
WORLD = [
    ("icon-eu-wx", "ICON-EU", 7.0, "dwd_icon_eu"),
    ("ecmwf", "ECMWF", 9.0, "ecmwf_ifs"),
    ("gfs", "GFS", 13.0, "ncep_gfs025"),
    ("icon", "ICON", 13 / 0.9, "dwd_icon"),
    ("gdps", "GDPS", 15 / 0.7, "cmc_gem_gdps"),
]
GLOBAL = {"icon-eu-wx", "ecmwf", "gfs", "icon", "gdps"}
SETS = {
    "europe": {"name": "mix-wx", "label": "Weather mix (Europe)", "zoom": 6, "models": EUROPE},
    "world": {"name": "mix-world-wx", "label": "Weather mix (world)", "zoom": 4, "models": WORLD},
}
CREDIT = ("The models' own (MET Norway, DMI, Météo-France, DWD, MeteoSwiss, KNMI, ItaliaMeteo, the Met Office, "
          "ECMWF, NOAA, ECCC); terrain: Copernicus GLO-90 DEM")
DAYS = 15
EVERY = 3 * 3600
# The ensembles' chance of rain (Open-Meteo's open data, data_spatial/<name>/: one file an hour or
# step, its precipitation_probability %): name, weight, grid (as model_heights.Grid's).
BUCKET = "https://openmeteo.s3.amazonaws.com/"
ROTATED_CH = "+proj=ob_tran +o_proj=longlat +o_lat_p=43 +o_lon_p=0 +lon_0=10 +R=6371229 +no_defs"
ENSEMBLES = [
    ("ecmwf_ifs025_ensemble", 2.0, {"kind": "latlon"}),
    ("ncep_gefs025", 1.0, {"kind": "latlon"}),
    ("cmc_gem_geps", 0.7, {"kind": "latlon"}),
    ("ukmo_global_ensemble_20km", 1.0, {"kind": "latlon"}),
    ("meteoswiss_icon_ch2_ensemble", 2.0, {"kind": "rotated", "proj": ROTATED_CH, "x0": -6.46, "y0": -4.06, "dx": 0.02, "dy": 0.02}),
    ("ukmo_uk_ensemble_2km", 2.0, {"kind": "wkt"}),
]


def ensemble_share(lead_hours):
    """How much the ensembles' chance counts against our models' vote: 30% for the first day (the
    fine models see the showers), rising to 80% from the third (the ensembles know the spread)."""
    return 0.3 if lead_hours < 24 else min(0.8, 0.3 + 0.5 * (lead_hours - 24) / 48)


class EnsembleChance:
    """One ensemble's newest run: its rain chance (0-1) at pixels for an hour, from the step that
    holds it (the chance over each step up to its time)."""

    def __init__(self, name, weight, spec):
        import world_tiles
        self.name, self.weight, self.spec = name, weight, spec
        self.files, self.grid, self.cache, self.places = {}, None, {}, {}
        now = datetime.now(timezone.utc)
        for back in range(3):
            day = (now - timedelta(days=back)).strftime("%Y/%m/%d/")
            runs = sorted({k.split("/")[5] for k in world_tiles.s3_list(f"data_spatial/{name}/{day}") if k.endswith(".om")}, reverse=True)
            for run in runs:
                keys = [k for k in world_tiles.s3_list(f"data_spatial/{name}/{day}{run}/") if k.endswith(".om") and "_" not in k.rsplit("/", 1)[-1]]
                if len(keys) >= 24:
                    for k in keys:
                        valid = datetime.strptime(k.rsplit("/", 1)[-1][:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
                        self.files[int(valid.timestamp())] = k
                    self.run = f"{day}{run}"
                    break
            if self.files:
                break
        self.times = sorted(self.files)
        if not self.times:
            raise RuntimeError("no run with its hours")

    def field(self, t):
        """The step's chance (%, the model's grid) holding hour t, or None."""
        later = next((x for x in self.times if x >= t), None)
        if later is None:
            return None
        i = self.times.index(later)
        step = later - self.times[i - 1] if i > 0 else 3600
        if later - t >= max(step, 3600) or step > MAX_GAP:
            return None
        if later not in self.cache:
            import tempfile, os
            import omfiles
            import model_heights
            handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
            handle.close()
            with open(handle.name, "wb") as out:
                out.write(get(BUCKET + self.files[later]))
            reader = omfiles.OmFileReader.from_path(handle.name)
            values = reader.get_child_by_name("precipitation_probability").read_array((slice(None), slice(None))).astype(np.float32)
            reader.close()
            os.unlink(handle.name)
            if self.grid is None:
                self.grid = model_heights.Grid(self.name, self.spec, values.shape)
            if len(self.cache) > 6:
                self.cache.pop(min(self.cache))
            self.cache[later] = values
        return self.cache[later]

    def at(self, t, lon, lat, key=None):
        """(chance 0-1, on its grid) at pixels, or None; `key` the block's (its pixels' places on the
        grid kept: the same every hour)."""
        import model_heights
        values = self.field(t)
        if values is None:
            return None
        placed = self.places.get(key) if key is not None else None
        if placed is None:
            col, row, on = self.grid.place(lon.ravel(), lat.ravel())
            placed = (np.asarray(col), np.asarray(row), np.asarray(on).reshape(lon.shape))
            if key is not None:
                if len(self.places) > 3000:
                    self.places.clear()
                self.places[key] = placed
        col, row, on = placed
        if not on.any():
            return None
        wrap = self.grid.kind == "latlon" and self.grid.nx * self.grid.dx > 359
        chance = model_heights.bilinear(values, col, row, wrap).reshape(lon.shape)
        on = on & np.isfinite(chance)
        return np.clip(np.nan_to_num(chance) / 100, 0, 1), on


class SpotRain:
    """The ensembles' weighted chance of rain (%) at every spot (the catalog's and the riders' public
    ones, spot_points.spots), hour by hour: points/ens-rain.json {set, run, updated, times, models,
    points: {"lat,lon": [chance or null, ...]}} (the spot's own place, spot_points.key)."""

    def __init__(self, ensembles):
        import spot_points
        self.ensembles = ensembles
        places = {spot_points.key(s["lat"], s["lon"]): (float(s["lat"]), float(s["lon"])) for s in spot_points.spots()}
        self.keys = sorted(places)
        self.lat = np.array([places[k][0] for k in self.keys], np.float64)
        self.lon = np.array([places[k][1] for k in self.keys], np.float64)
        self.times, self.values = [], []

    def add(self, t):
        if not self.keys:
            return
        total, weights = np.zeros(len(self.keys), np.float32), np.zeros(len(self.keys), np.float32)
        for e in self.ensembles:
            try:
                got = e.at(t, self.lon, self.lat, key="spots")
            except Exception as error:
                print(f"{e.name} at the spots: left out ({error})", flush=True)
                got = None
            if got is None:
                continue
            value, on = got
            total += e.weight * on * value
            weights += e.weight * on
        self.times.append(int(t))
        self.values.append(np.where(weights > 0, np.round(100 * total / np.maximum(weights, 1e-9)), np.nan))

    def publish(self, local=None):
        if not self.times:
            return
        grid = np.array(self.values)
        points = {k: [None if np.isnan(v) else int(v) for v in grid[:, i]] for i, k in enumerate(self.keys)}
        body = json.dumps({"set": "ens-rain", "run": datetime.fromtimestamp(self.times[0], timezone.utc).strftime("%Y%m%dT%HZ"),
                           "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "times": self.times,
                           "models": [e.name for e in self.ensembles], "points": points}, separators=(",", ":")).encode()
        if local:
            Path(local).mkdir(parents=True, exist_ok=True)
            (Path(local) / "ens-rain.json").write_bytes(body)
        else:
            import world_tiles
            world_tiles.upload("points/ens-rain.json", body)
        print(f"ens-rain: {len(self.keys)} spots, {len(self.times)} hours", flush=True)


def get(url):
    return mt.get(url)


# MARK: Reading a pack over a block

def decode_temp(w):
    known = w[..., 0] > 0
    air = np.where(known, (w[..., 0] - 1) / 3 - 40, np.nan)
    feels = np.where(known & (w[..., 1] > 0), (w[..., 1] - 1) / 3 - 40, air)
    hpa = np.where(w[..., 2] > 0, w[..., 2] / 2 + 940, np.nan)
    return {"air": air, "feels": feels, "hpa": hpa}


def decode_rain(w):
    return {"rate": (w[..., 0] / 50) ** 2, "cloud": w[..., 1] / 255}


def decode_height(w):
    return {"h": w[..., 0] * 256 + w[..., 1] - 1000}


def block(pack, z, px0, py0, w, h, decode):
    """A pack's values over a block of zoom-z pixels (px0, py0: its top left at 256 a tile), bilinear
    from its finest zoom up to z, each value where known and where the pack's alpha says it counts:
    ({name: array, NaN unknown}, weight 0-1), or None where it has nothing."""
    zm = min(z, pack.top)
    size = pack.sizes.get(zm, SIZE)
    scale = 2 ** (z - zm)
    n = 2 ** zm
    xs = (px0 + np.arange(w) + 0.5) / SIZE / scale * size - 0.5
    ys = (py0 + np.arange(h) + 0.5) / SIZE / scale * size - 0.5
    c0, c1 = int(math.floor(xs[0])) - 1, int(math.floor(xs[-1])) + 2
    r0, r1 = int(math.floor(ys[0])) - 1, int(math.floor(ys[-1])) + 2
    window = np.zeros((r1 - r0 + 1, c1 - c0 + 1, 4), np.float32)
    found = False
    for ty in range(r0 // size, r1 // size + 1):
        if not (0 <= ty < n):
            continue
        for tx in range(c0 // size, c1 // size + 1):
            t = pack.tile(zm, tx % n, ty)
            if t is None or t.shape[0] != size:
                continue
            a0, a1 = max(r0, ty * size), min(r1, ty * size + size - 1)
            b0, b1 = max(c0, tx * size), min(c1, tx * size + size - 1)
            window[a0 - r0:a1 - r0 + 1, b0 - c0:b1 - c0 + 1] = t[a0 - ty * size:a1 - ty * size + 1, b0 - tx * size:b1 - tx * size + 1]
            found = True
    if not found:
        return None
    alpha = window[..., 3] / 255
    values = decode(window)
    if scale == 1 and size == SIZE:
        crop = (slice(1, 1 + h), slice(1, 1 + w))
        weight = alpha[crop]
        if weight.max() <= 0.004:
            return None
        return {k: np.where(weight > 0, v[crop], np.nan).astype(np.float32) for k, v in values.items()}, weight
    gy, gx = np.meshgrid(ys - r0, xs - c0, indexing="ij")

    def at(values_):
        return ndimage.map_coordinates(values_, [gy, gx], order=1, mode="nearest").astype(np.float32)
    weight = at(alpha)
    if weight.max() <= 0.004:
        return None
    out = {}
    for k, v in values.items():
        known = alpha * np.isfinite(v)
        share = at(known)
        out[k] = np.where(share > 1e-3, at(np.nan_to_num(v) * known) / np.maximum(share, 1e-6), np.nan)
    return out, np.clip(weight, 0, 1)


class Terrain:
    """Our terrain's mean height over a block (tiles/terrain: low.pack to zoom 5, 5-x-y.pack 6-9)."""

    def __init__(self):
        index = json.loads(get(FILES + "terrain/index.json"))
        self.packs = set(index["packs"])
        self.low = mt.Pack(get(FILES + "terrain/low.pack"))
        self.cache = {}

    def pack(self, z, x, y):
        if z <= 5:
            return self.low
        f = 2 ** (z - 5)
        name = f"{x // f}-{y // f}"
        if name not in self.packs:
            return None
        if name not in self.cache:
            self.cache[name] = mt.Pack(get(FILES + f"terrain/5-{name}.pack"))
        return self.cache[name]

    def heights(self, z, px0, py0, w, h):
        out = np.zeros((h, w), np.float32)
        n = 2 ** z
        for ty in range(py0 // SIZE, (py0 + h - 1) // SIZE + 1):
            if not (0 <= ty < n):
                continue
            for tx in range(px0 // SIZE, (px0 + w - 1) // SIZE + 1):
                pack = self.pack(z, tx % n, ty)
                tile = pack.tile(z, tx % n, ty) if pack is not None else None
                if tile is None:
                    continue
                a0, a1 = max(py0, ty * SIZE), min(py0 + h, ty * SIZE + SIZE)
                b0, b1 = max(px0, tx * SIZE), min(px0 + w, tx * SIZE + SIZE)
                part = tile[a0 - ty * SIZE:a1 - ty * SIZE, b0 - tx * SIZE:b1 - tx * SIZE]
                out[a0 - py0:a1 - py0, b0 - px0:b1 - px0] = part[..., 0] * 256 + part[..., 1] - 1000
        return out


class Model:
    """One weather set's hours (its frames' temp and rain packs) and its ground height."""

    def __init__(self, name, label, km, heights):
        self.name, self.label, self.weight = name, label, 1 / km
        index = json.loads(get(FILES + f"{name}/index.json"))
        self.run = index.get("runName")
        self.temp = {f["time"]: f["temp"] for f in index["frames"] if f.get("temp")}
        self.rain = {f["time"]: f["rain"] for f in index["frames"] if f.get("rain")}
        self.temp_times, self.rain_times = sorted(self.temp), sorted(self.rain)
        self.packs = {}
        try:
            self.height = mt.Pack(get(FILES + f"heights/{heights}.pack"))
        except Exception as error:
            print(f"{label}: no ground height ({error}); its air as it is", flush=True)
            self.height = None
        self.height_blocks = {}

    def pack(self, file):
        if file not in self.packs:
            self.packs[file] = mt.Pack(get(FILES + f"{self.name}/{file}"))
        return self.packs[file]

    def temp_hours(self, t):
        """[(file, share)] for the air at t: its hour, or the two round it (6 hours apart at most)."""
        if t in self.temp:
            return [(self.temp[t], 1.0)]
        later = next((x for x in self.temp_times if x > t), None)
        earlier = next((x for x in reversed(self.temp_times) if x < t), None)
        if later is None or earlier is None or later - earlier > MAX_GAP:
            return []
        share = (t - earlier) / (later - earlier)
        return [(self.temp[earlier], 1 - share), (self.temp[later], share)]

    def rain_hour(self, t):
        """The rain pack whose step holds the hour up to t (a rate over its step), or None."""
        later = next((x for x in self.rain_times if x >= t), None)
        if later is None:
            return None
        i = self.rain_times.index(later)
        step = later - self.rain_times[i - 1] if i > 0 else 3600
        return self.rain[later] if later - t < max(step, 3600) and step <= MAX_GAP else None

    def files(self, t):
        hours = [f for f, _ in self.temp_hours(t)]
        rain = self.rain_hour(t)
        return hours + ([rain] if rain else [])

    def forget(self, keep):
        for file in [f for f in self.packs if f not in keep]:
            del self.packs[file]

    def ground(self, z, px0, py0, w, h):
        key = (z, px0, py0)
        if key not in self.height_blocks:
            got = block(self.height, z, px0, py0, w, h, decode_height) if self.height is not None else None
            self.height_blocks[key] = got[0]["h"] if got is not None else None
            if len(self.height_blocks) > 4000:
                self.height_blocks.clear()
        return self.height_blocks[key]


def tile_km(z, y):
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / 2 ** z))))
    return 40075.016 * math.cos(math.radians(lat)) / (2 ** z * SIZE)


def weighted_median(values, weights):
    """Per pixel: the value where the weights' running sum passes half (values, weights: [models, h, w])."""
    order = np.argsort(values, axis=0)
    v = np.take_along_axis(values, order, axis=0)
    wt = np.take_along_axis(weights, order, axis=0)
    cum = np.cumsum(wt, axis=0)
    half = cum[-1] / 2
    idx = np.argmax(cum >= half[None], axis=0)
    return np.take_along_axis(v, idx[None], axis=0)[0]


def block_lonlat(z, px0, py0, w, h):
    n = 2 ** z * SIZE
    lon = (px0 + np.arange(w) + 0.5) / n * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (py0 + np.arange(h) + 0.5) / n))))
    return np.broadcast_to(lon, (h, w)), np.broadcast_to(lat[:, None], (h, w))


def mix_tile(models, t, terrain, z, x, y, ensembles=(), lead=0.0):
    """One tile: (temp RGBA, rain RGBA, the models in it, checks), or None where no model is."""
    px0, py0 = x * SIZE - MARGIN, y * SIZE - MARGIN
    w = h = SIZE + 2 * MARGIN
    ground = terrain.heights(z, px0, py0, w, h) if terrain is not None else None
    near = max(1, int(round(NEAR_KM / tile_km(z, y))))
    zero = np.zeros((h, w), np.float32)
    tw, tsum, fsum, pw, psum = zero.copy(), zero.copy(), zero.copy(), zero.copy(), zero.copy()
    rw, csum, chance, mean = zero.copy(), zero.copy(), zero.copy(), zero.copy()
    rates, rweights, used = [], [], []
    for m in models:
        hours = m.temp_hours(t)
        got_t = None
        for file, share in hours:
            got = block(m.pack(file), z, px0, py0, w, h, decode_temp)
            if got is None:
                got_t = None
                break
            values, weight = got
            if got_t is None:
                got_t = ({k: v * share for k, v in values.items()}, weight * share)
            else:
                got_t = ({k: got_t[0][k] + v * share for k, v in values.items()}, got_t[1] + weight * share)
        if got_t is not None:
            values, weight = got_t
            mg = m.ground(z, px0, py0, w, h)
            shift = LAPSE * (mg - ground) if mg is not None and ground is not None else 0
            # The air's weight (the owner, 18.10.2026, checked against Open-Meteo at 13 places): MET
            # Nordic's counted TEMP_BOOST times (corrected with thousands of stations' readings: the
            # mix had pulled Riga 0.5 and Oslo 0.7 °C off it), and a model whose own ground is far from
            # ours less, halving about every 200 m (a coarse model's air moved down a valley 6.5 °C a
            # km made Innsbruck's autumn nights 2 °C warm; the fine Alpine models lead there).
            gap = np.abs(mg - ground) if mg is not None and ground is not None else 0
            ok = np.isfinite(values["air"])
            wt = np.where(ok, m.weight * TEMP_BOOST.get(m.name, 1.0) * np.maximum(np.exp(-gap / HEIGHT_SCALE), 0.01) * weight, 0)
            tw += wt
            tsum += wt * np.nan_to_num(values["air"] + shift)
            fsum += wt * np.nan_to_num(values["feels"] + shift)
            has_p = np.isfinite(values["hpa"])
            pw += np.where(has_p, wt, 0)
            psum += np.where(has_p, wt * np.nan_to_num(values["hpa"]), 0)
            if (weight[MARGIN:-MARGIN, MARGIN:-MARGIN] > 0.02).any():
                used.append(m.label)
        file = m.rain_hour(t)
        got = block(m.pack(file), z, px0, py0, w, h, decode_rain) if file else None
        if got is not None:
            values, weight = got
            rate = np.maximum(np.nan_to_num(values["rate"]), 0)
            wr = np.where(np.isfinite(values["rate"]), m.weight * weight, 0)
            wet = rate >= WET
            close = ndimage.maximum_filter(rate, size=2 * near + 1) >= WET
            rw += wr
            csum += wr * np.nan_to_num(values["cloud"])
            chance += wr * (wet + 0.5 * (close & ~wet))
            mean += wr * rate
            rates.append(rate)
            rweights.append(wr)
    c = (slice(MARGIN, MARGIN + SIZE), slice(MARGIN, MARGIN + SIZE))
    if tw[c].max() <= 1e-6:
        return None
    safe = np.maximum(tw, 1e-9)
    air = np.where(tw > 0, tsum / safe, np.nan)[c]
    feels = np.where(tw > 0, fsum / safe, np.nan)[c]
    hpa = np.where(pw > 0, psum / np.maximum(pw, 1e-9), np.nan)[c]
    temp = np.concatenate([tiler.temp_rgb(air, feels, hpa), np.where(np.isfinite(air), 255, 0)[..., None]], axis=-1).astype(np.uint8)
    rain = None
    if rates:
        rs = np.maximum(rw, 1e-9)
        typical = weighted_median(np.stack(rates)[:, c[0], c[1]], np.stack(rweights)[:, c[0], c[1]])
        cloud = (csum / rs)[c]
        p = np.clip(chance / rs, 0, 1)
        # The ensembles' own chance, more of it the further ahead.
        if ensembles:
            lon, lat = block_lonlat(z, px0, py0, w, h)
            enum, ew = np.zeros((h, w), np.float32), np.zeros((h, w), np.float32)
            for e in ensembles:
                try:
                    got = e.at(t, lon, lat, key=(z, px0, py0, w, h))
                except Exception as error:
                    print(f"{e.name}: left out ({error})", flush=True)
                    got = None
                if got is None:
                    continue
                value, on = got
                weight = e.weight * on
                enum += weight * value
                ew += weight
            share = ensemble_share(lead)
            p = np.where(ew > 0, (1 - share) * p + share * enum / np.maximum(ew, 1e-9), p)
        p = p[c]
        avg = (mean / rs)[c]
        have = rw[c] > 0
        rain = np.stack([np.clip(np.rint(np.sqrt(typical) * 50), 0, 255), np.clip(np.rint(cloud * 255), 0, 255),
                         np.clip(np.rint(p * 255), 0, 255),
                         np.where(have, 128 + np.clip(np.rint(np.sqrt(avg) * 40), 0, 127), 0)], axis=-1).astype(np.uint8)
    return temp, rain, used, (air[np.isfinite(air)][::17], np.sqrt(np.maximum(avg, 0))[::17].ravel() if rain is not None else np.zeros(0))


def parent(children, kind):
    """A tile from its four children ({(dx, dy): rgba}), two by two (the known pixels' mean)."""
    big = np.zeros((SIZE * 2, SIZE * 2, 4), np.float32)
    for (dx, dy), rgba in children.items():
        big[dy * SIZE:dy * SIZE + SIZE, dx * SIZE:dx * SIZE + SIZE] = rgba
    known = (big[..., 3] > 0).astype(np.float32)
    count = known.reshape(SIZE, 2, SIZE, 2).sum(axis=(1, 3))
    out = np.zeros((SIZE, SIZE, 4), np.float32)
    for ch in range(4):
        values = big[..., ch]
        if kind == "temp" and ch < 3:
            # 0 means unknown in the air's and pressure's bytes: only the known ones averaged.
            k = known * (values > 0)
            n = k.reshape(SIZE, 2, SIZE, 2).sum(axis=(1, 3))
            out[..., ch] = np.where(n > 0, (values * k).reshape(SIZE, 2, SIZE, 2).sum(axis=(1, 3)) / np.maximum(n, 1), 0)
        else:
            out[..., ch] = np.where(count > 0, (values * known).reshape(SIZE, 2, SIZE, 2).sum(axis=(1, 3)) / np.maximum(count, 1), 0)
    out[..., 3] = np.where(count > 0, np.maximum(out[..., 3], 128 if kind == "rain" else 255), 0)
    return np.rint(out).astype(np.uint8)


def published_index(name):
    try:
        request = urllib.request.Request(FILES + f"{name}/index.json?t={int(time.time())}", headers={"User-Agent": mt.AGENT})
        with urllib.request.urlopen(request, timeout=30) as reply:
            index = json.load(reply)
        return index.get("inputs") or {}, mt.run_unix(index.get("updated")) or 0
    except Exception:
        return {}, 0


def main():
    args = sys.argv[1:]
    if not args or args[0] not in SETS:
        raise SystemExit(__doc__)
    spec = SETS[args[0]]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    started = time.time()
    models = []
    for name, label, km, heights in spec["models"]:
        try:
            m = Model(name, label, km, heights)
            if m.temp_times:
                models.append(m)
            else:
                print(f"{label}: no air layers", flush=True)
        except Exception as error:
            print(f"{label}: no index ({error})", flush=True)
    if not any(m.name in ("ecmwf", "gfs") for m in models):
        raise SystemExit("No world model to mix")
    inputs = {m.name: m.run for m in models}
    if local is None and "--force" not in args:
        before, made = published_index(spec["name"])
        if before == inputs:
            print(f"{spec['name']}: its models' runs are published already")
            return
        if set(before) == set(inputs) and time.time() - made < EVERY:
            print(f"{spec['name']}: made {(time.time() - made) / 3600:.1f} h ago, the next after {EVERY // 3600} h")
            return
    now = int(time.time()) // 3600 * 3600
    if args[0] == "europe":
        regional = [m for m in models if m.name not in GLOBAL]
        if not regional:
            raise SystemExit("No regional model to mix")
        last = max(m.temp_times[-1] for m in regional)
        hours = list(range(now, last + 1, 3600))
    else:
        last = min(max(m.temp_times[-1] for m in models), now + DAYS * 86400)
        hours = [t for t in range(now, last + 1, 3600)
                 if t - now < 5 * 86400 or (t - now < 6 * 86400 and t % (3 * 3600) == 0) or t % (6 * 3600) == 0]
    if limit:
        hours = hours[:limit]
    try:
        terrain = Terrain()
    except Exception as error:
        if local is None:
            raise SystemExit(f"No terrain yet ({error}): the mix waits for it (terrain.yml)")
        print(f"No terrain ({error}): the air as each model has it", flush=True)
        terrain = None
    ensembles = []
    for name, weight, grid_spec in ENSEMBLES:
        try:
            ensembles.append(EnsembleChance(name, weight, grid_spec))
            print(f"{name}: {ensembles[-1].run}, {len(ensembles[-1].times)} steps", flush=True)
        except Exception as error:
            print(f"{name}: no chance of rain ({error})", flush=True)
    # The ensembles' own chance at the spots (the owner, 19.10.2026: the spot page's chance of rain as
    # the Weather tab's), each hour the world's mix makes, from the same files: points/ens-rain.json.
    spot_rain = SpotRain(ensembles) if args[0] == "world" and ensembles else None
    top = spec["zoom"]
    zooms = range(top - 2, top + 1) if args[0] == "europe" else range(0, top + 1)
    keys = tiler.tiles_in_box(mt.BOX, [top]) if args[0] == "europe" else [(top, x, y) for x in range(2 ** top) for y in range(2 ** top)]
    run_time = datetime.fromtimestamp(now, timezone.utc)
    out = tiler.Run(spec["name"], run_time, zooms, spec["label"], CREDIT, True, local)
    print(f"{spec['name']} {out.folder}: {len(keys)} tiles at zoom {top}, {len(hours)} hours, models: "
          + ", ".join(f"{m.label} {m.run}" for m in models), flush=True)
    for t in hours:
        needed = {m: m.files(t) for m in models}
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda job: job[0].pack(job[1]), [(m, f) for m, files in needed.items() for f in files]))
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda key: (key, mix_tile(models, t, terrain, *key, ensembles=ensembles, lead=(t - now) / 3600)), keys))
        for m in models:
            m.forget(set(needed[m]))
        if spot_rain:
            spot_rain.add(t)
        levels = {"temp": {}, "rain": {}}
        used, airs, rains = set(), [], []
        for key, result in results:
            if result is None:
                continue
            temp, rain, names, (air, wet) = result
            levels["temp"][key[1:]] = temp
            if rain is not None:
                levels["rain"][key[1:]] = rain
            used.update(names)
            airs.append(air)
            rains.append(wet)
        if not levels["temp"]:
            continue
        celsius = np.concatenate(airs) if airs else np.zeros(0)
        mm = np.concatenate(rains) ** 2 if rains else np.zeros(0)
        if not out.checked(spec["label"], t, quality.weather(celsius, mm)):
            continue
        for kind in ("temp", "rain"):
            level = levels[kind]
            if not level:
                continue
            tiles = {(top, x, y): rgba for (x, y), rgba in level.items()}
            for z in range(top - 1, zooms.start - 1, -1):
                upper = {}
                for x, y in {(x // 2, y // 2) for x, y in level}:
                    children = {(dx, dy): level[(2 * x + dx, 2 * y + dy)] for dx in (0, 1) for dy in (0, 1)
                                if (2 * x + dx, 2 * y + dy) in level}
                    upper[(x, y)] = parent(children, kind)
                level = upper
                tiles.update({(z, x, y): rgba for (x, y), rgba in level.items()})
            out.put(t, tiler.world_tiles.encode({k: tiler.world_tiles.png(v, "RGBA") for k, v in tiles.items()},
                                                {z: SIZE for z in zooms}), kind)
        out.frames[t]["models"] = [m.label for m in models if m.label in used]
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%d %H:%M}: {len(levels['temp'])} tiles, "
              f"{', '.join(out.frames[t]['models'])}, {time.time() - started:.0f} s", flush=True)
    out.finish(weights={m.label: round(m.weight, 4) for m in models}, inputs=inputs)
    if spot_rain:
        spot_rain.publish(local)
    print(f"{spec['name']} done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up: their exit can crash Python after the work is published
    # ("double free or corruption", 18.10.2026), failing the run and the workflow's next step.
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
