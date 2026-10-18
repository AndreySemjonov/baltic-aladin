"""Our models' wind at the spots' points (the owner, 18.10.2026: step 2 of leaving Open-Meteo, the
spot page's wind from our own data): after each run of a model's tiles, its wind at every point a
spot reads it at (the app's catalog spots and the riders' public ones: each model's point, its
csPoints, and the spot itself for "Sea wind" off), every hour the tiles have, from its finest tiles'
nearest pixel; published as points/<set>.json through station-history, which puts it in the spots'
forecasts in place of Open-Meteo's wind for these models.

    {"set", "run", "updated", "times": [unix...], "points": {"<lat>,<lon>": [[east, north, gust] | null, ...]}}

m/s; the point's key its latitude and longitude to 4 decimals, no trailing zeros (as station-history
writes them). The hours before the run's first are kept from the file before (a day and a half), as
Open-Meteo's answers have the day before.

The weather too (step 3, the owner, 18.10.2026): the air, rain and clouds of the models whose weather
we make (ECMWF, ICON-EU, MET Nordic 1 km) at each spot ([air °C, rain mm an hour, cloud %, low, mid,
high %], points/<name>.json: ecmwf-wx, icon-eu-wx, metnordic-1km-wx), and the waves at each spot (EWAM
over Europe's seas, ECMWF's elsewhere: [height m, period s, direction °], the nearest sea pixel within
about 10 km: points/ewam.json, points/ecmwf-waves.json).

    python spot_points.py [set ...] [--force] [--local DIR]
"""
import io
import json
import math
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import maplib
import world_tiles

FILES = "https://files.meteogust.com/"
CATALOG = "https://data.meteogust.com/spots/catalog"
PUBLIC = "https://data.meteogust.com/spots/public"
# The app's Open-Meteo model keys and our sets of the same models (FMI's and ALADIN's come from
# their own files already).
SETS = {
    "dmi_harmonie_arome_europe": "dini", "metno_nordic": "metnordic-1km", "meteofrance_arome_france_hd": "arome",
    "knmi_harmonie_arome_netherlands": "harm-nl", "icon_d2": "icon-d2", "meteoswiss_icon_ch1": "icon-ch1",
    "italia_meteo_arpae_icon_2i": "icon-2i", "icon_eu": "icon-eu", "ecmwf_ifs": "ecmwf", "gfs_global": "gfs",
    "icon_global": "icon", "gem_global": "gdps",
}
KEEP_BEFORE = 36 * 3600
# The weather: its points file's name, its tiles' folder, the app's model key, the frames' packs.
WEATHER = {
    "ecmwf-wx": ("ecmwf", "ecmwf_ifs", ("temp", "rain", "cloud")),
    "icon-eu-wx": ("icon-eu-wx", "icon_eu", ("temp", "rain", "cloud")),
    "metnordic-1km-wx": ("metnordic-1km-wx", "metno_nordic", ("temp", "rain")),
    # The world's other models (world_tiles.py since 18.10.2026).
    "gfs-wx": ("gfs", "gfs_global", ("temp", "rain")),
    "icon-wx": ("icon", "icon_global", ("temp", "rain")),
    "gdps-wx": ("gdps", "gem_global", ("temp", "rain")),
}
WAVES = ("ewam", "ecmwf-waves")
# Waves: the nearest sea pixel within this many pixels at the set's finest zoom (EWAM's zoom 6 about
# 1.3 km a pixel in the Baltic: 8 pixels about 10 km; ECMWF's zoom 3 about 15 km: 1).
WAVE_SEARCH = {"ewam": 8, "ecmwf-waves": 1}


def get(url, start=None, length=None, tries=4):
    headers = {"User-Agent": maplib.USER_AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{start + length - 1}"
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if attempt == tries - 1:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == tries - 1:
                raise
        time.sleep(3 * (attempt + 1))


def key(lat, lon):
    """"57.0602,24.0207": 4 decimals, no trailing zeros (JavaScript's +x.toFixed(4))."""
    def short(v):
        text = f"{round(float(v), 4):.4f}".rstrip("0").rstrip(".")
        return "0" if text in ("-0", "") else text
    return short(lat) + "," + short(lon)


def spots():
    """The catalog's spots and the riders' public ones (their weights and points)."""
    out = []
    for url, field in ((CATALOG, "spots"), (PUBLIC, "spots")):
        try:
            for entry in json.loads(get(url))[field]:
                spot = entry.get("spot") or {}
                if isinstance(spot.get("weights"), dict) and spot.get("lat") is not None:
                    out.append(spot)
        except Exception as error:
            print(f"{url}: {error}", flush=True)
    return out


def wanted(spots_list):
    """{set: {point key: (lat, lon)}}: each model's point at each spot, and the spot itself."""
    out = {}
    for spot in spots_list:
        lat, lon = float(spot["lat"]), float(spot["lon"])
        cs = spot.get("csPoints") or {}
        for model in spot["weights"]:
            if model not in SETS:
                continue
            places = out.setdefault(SETS[model], {})
            places[key(lat, lon)] = (lat, lon)
            point = cs.get(model)
            if isinstance(point, list) and len(point) == 2:
                places[key(*point)] = (float(point[0]), float(point[1]))
    return out


class PackReader:
    """A published pack's header and single tiles, by range requests."""

    def __init__(self, url):
        self.url = url
        head = get(url, 0, 65536)
        if head is None or len(head) < 8:
            raise ValueError("no pack")
        n = struct.unpack("<I", head[4:8])[0]
        if 8 + n > len(head):
            head = get(url, 0, 8 + n)
        header = json.loads(head[8:8 + n])
        self.tiles, self.start = header["tiles"], 8 + n
        self.sizes = {int(z): s for z, s in header.get("sizes", {}).items()}

    def tile(self, z, x, y):
        at = self.tiles.get(f"{z}/{x}/{y}")
        if at is None:
            return None
        data = get(self.url, self.start + at[0], at[1])
        return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA")) if data else None


def place(lat, lon, z, size):
    """The tile and its nearest pixel at zoom z."""
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    tx, ty = int(x), int(y)
    return tx, ty, min(int((x - tx) * size), size - 1), min(int((y - ty) * size), size - 1)


def wind(r, g, b, a):
    return None if a < 128 else [round((r - 128) / 4, 2), round((g - 128) / 4, 2), round(b / 5, 2)]


def air(r, g, b, a):
    return None if a < 128 or r == 0 else [round((r - 1) / 3 - 40, 1)]


def rain(r, g, b, a):
    return None if a < 128 else [round((r / 50) ** 2, 2), round(g / 2.55)]


def clouds(r, g, b, a):
    return None if a < 128 else [round(r / 2.55), round(g / 2.55), round(b / 2.55)]


def waves(r, g, b, a):
    return None if a < 128 or r == 0 else [round((r - 1) / 20, 2), round(g / 10, 1), round(b * 360 / 256)]


def frame_values(url, points, zooms, sizes, decode=wind, search=0):
    """One hour's values (decode(r, g, b, a), or None) at each point: the finest zoom that has its
    tile; `search`: else the nearest pixel with a value within that many (the waves' sea)."""
    try:
        pack = PackReader(url)
    except Exception:
        return {k: None for k in points}
    out, tiles = {}, {}
    for k, (lat, lon) in points.items():
        out[k] = None
        for z in range(zooms[1], zooms[0] - 1, -1):
            size = pack.sizes.get(z) or sizes.get(z) or 256
            tx, ty, px, py = place(lat, lon, z, size)
            if f"{z}/{tx}/{ty}" not in pack.tiles:
                continue
            if (z, tx, ty) not in tiles:
                tiles[(z, tx, ty)] = pack.tile(z, tx, ty)
            pixels = tiles[(z, tx, ty)]
            if pixels is None or pixels.shape[0] != size:
                continue
            found = decode(*(int(c) for c in pixels[py, px]))
            for ring in range(1, search + 1):
                if found is not None:
                    break
                best = None
                for dy in range(-ring, ring + 1):
                    for dx in range(-ring, ring + 1):
                        if max(abs(dx), abs(dy)) != ring or not (0 <= py + dy < size and 0 <= px + dx < size):
                            continue
                        value = decode(*(int(c) for c in pixels[py + dy, px + dx]))
                        if value is not None and (best is None or dx * dx + dy * dy < best[0]):
                            best = (dx * dx + dy * dy, value)
                found = best[1] if best else None
            out[k] = found
            break
    return out


def weather_values(folder, frame, points, zooms, sizes, layers):
    """One hour's [air, rain, cloud, low, mid, high] at each point from its temp, rain and cloud packs."""
    parts = {}
    for layer, decode in (("temp", air), ("rain", rain), ("cloud", clouds)):
        if layer in layers and frame.get(layer):
            parts[layer] = frame_values(FILES + f"tiles/{folder}/{frame[layer]}", points, zooms, sizes, decode)
    out = {}
    for k in points:
        t, r, c = (parts.get(layer, {}).get(k) for layer in ("temp", "rain", "cloud"))
        if t is None and r is None:
            out[k] = None
            continue
        out[k] = [t[0] if t else None, r[0] if r else None, r[1] if r else None] + (c if c else [None, None, None])
    return out


def published(set_name):
    data = get(FILES + f"points/{set_name}.json?t={int(time.time())}")
    try:
        return json.loads(data) if data else None
    except ValueError:
        return None


def make(set_name, points, force=False, folder=None, values_of=None):
    """The set's points file, or None when the newest run's is published for these points; `folder`:
    its tiles' (when the file's name differs), `values_of(frame, zooms, sizes)`: an hour's values."""
    folder = folder or set_name
    index_data = get(FILES + f"tiles/{folder}/index.json?t={int(time.time())}")
    if not index_data:
        print(f"{set_name}: no index", flush=True)
        return None
    index = json.loads(index_data)
    run = index.get("runName")
    before = published(set_name)
    if not force and before and before.get("run") == run and set(points) <= set(before.get("points", {})):
        print(f"{set_name}: {run} published already", flush=True)
        return None
    frames = sorted((f for f in index["frames"] if f.get("file")), key=lambda f: f["time"])
    zooms = index.get("zooms") or [0, 6]
    sizes = {int(z): s for z, s in (index.get("tileSizes") or {}).items()}
    started = time.time()
    values_of = values_of or (lambda f, zooms, sizes: frame_values(FILES + f"tiles/{folder}/{f['file']}", points, zooms, sizes))
    with ThreadPoolExecutor(16) as pool:
        values = list(pool.map(lambda f: values_of(f, zooms, sizes), frames))
    times = [f["time"] for f in frames]
    series = {k: [v[k] for v in values] for k in points}
    # The hours before this run's first, from the file before (the day before, as Open-Meteo's).
    if before and times:
        keep = [i for i, t in enumerate(before.get("times", [])) if times[0] - KEEP_BEFORE <= t < times[0]]
        if keep:
            old_times = [before["times"][i] for i in keep]
            for k in series:
                old = before.get("points", {}).get(k)
                series[k] = ([old[i] for i in keep] if old else [None] * len(keep)) + series[k]
            times = old_times + times
    found = sum(1 for k in series if any(v is not None for v in series[k]))
    print(f"{set_name} {run}: {len(points)} points ({found} with data), {len(times)} hours, {time.time() - started:.0f} s", flush=True)
    return {"set": set_name, "run": run, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "times": times, "points": series}


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    chosen = [a for a in args if not a.startswith("--") and (local is None or a != str(local))]
    every = spots()
    by_set = wanted(every)
    force = "--force" in args or local is not None

    def publish(name, result):
        if result is None:
            return
        body = json.dumps(result, separators=(",", ":")).encode()
        if local:
            local.mkdir(parents=True, exist_ok=True)
            (local / f"{name}.json").write_bytes(body)
        else:
            print(world_tiles.upload(f"points/{name}.json", body).strip(), flush=True)

    for set_name in sorted(set(SETS.values())):
        if chosen and set_name not in chosen:
            continue
        points = by_set.get(set_name)
        if points:
            publish(set_name, make(set_name, points, force))
    # The weather at the spots whose models it is; the waves at every spot.
    for name, (folder, model, layers) in WEATHER.items():
        if chosen and name not in chosen:
            continue
        points = {key(s["lat"], s["lon"]): (float(s["lat"]), float(s["lon"])) for s in every if model in s["weights"]}
        if points:
            publish(name, make(name, points, force, folder,
                               lambda f, zooms, sizes, points=points, folder=folder, layers=layers:
                               weather_values(folder, f, points, zooms, sizes, layers)))
    sea = {key(s["lat"], s["lon"]): (float(s["lat"]), float(s["lon"])) for s in every}
    for name in WAVES:
        if chosen and name not in chosen:
            continue
        publish(name, make(name, sea, force, None,
                           lambda f, zooms, sizes, name=name: frame_values(FILES + f"tiles/{name}/{f['file']}", sea, zooms, sizes,
                                                                          waves, WAVE_SEARCH[name])))


if __name__ == "__main__":
    main()
