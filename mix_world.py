"""The world mix as tiles (the owner, 18.10.2026: "make mix all the way long to match our forecast
page", a mix of its own in each region): the mix's rules (mix_tiles.py) on the world's models,
everywhere and to 14 days, under the Baltic and Europe Mix (which has the regional 1-3 km models
for its first 2-3 days):

- the models: ICON-EU over Europe (to 5 days), ECMWF, GFS, ICON and GDPS everywhere (our world
  tiles, world_tiles.py); each place's mix is whichever of them reach it;
- the weights: near the app's catalog spots their own, elsewhere the WG formula (1 / the model's
  grid in km, as the app's SpotWeights; the owner, 18.10.2026: the formula everywhere else);
- a coarse model's land pulled back to the coastline (our coastline tiles), the direction a
  weighted vector mean, the 3- and 6-hourly hours interpolated (not over more than 6 hours);
- the hours from now every hour to 5 days, every 3 hours to 6, then every 6 hours to 14 days;
- made at zoom 4 (about 10 km a pixel at the equator, 5 km in the Baltic), zooms 3-0 from it two
  by two; published as tiles/mix-world when any of its models has a new run.

    python mix_world.py [--force] [--local DIR] [--hours N]
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

import mix_tiles as mt
import quality
import tiler

# Set, name, the model's grid (km, as mix_tiles.APP_GRIDS: the app's model factors folded in), how
# far its land reaches over the sea (km, about 1.5 of its cells).
WORLD_MODELS = [
    ("icon-eu", "ICON-EU", 7.0, 10.0),
    ("ecmwf", "ECMWF", 9.0, 25.0),
    ("gfs", "GFS", 13.0, 25.0),
    ("icon", "ICON", 13 / 0.9, 20.0),
    ("gdps", "GDPS", 15 / 0.7, 22.0),
]
Z = 4
ZOOMS = range(0, Z + 1)
SIZE = 256
DAYS = 14
CREDIT = ("ICON-EU and ICON, Deutscher Wetterdienst; ECMWF open data (CC BY 4.0); GFS, NOAA; "
          "GDPS, Environment and Climate Change Canada (Open Government Licence - Canada)")
PUBLISHED = "https://files.meteogust.com/tiles/mix-world/index.json"


# A new world mix at most this often when only its models' runs changed (each run's taps are made
# ahead on our server, station-history seriesWarm, a few hours' work): about four a day.
EVERY = 5 * 3600


def published_index():
    """The published run's models' runs ({set: runName}) and when it was made (Unix s, 0: none)."""
    try:
        request = urllib.request.Request(PUBLISHED + "?t=%d" % time.time(), headers={"User-Agent": mt.AGENT})
        with urllib.request.urlopen(request, timeout=30) as reply:
            index = json.load(reply)
        return index.get("inputs") or {}, mt.run_unix(index.get("updated")) or 0
    except Exception:
        return {}, 0


def decode(rgba):
    """A tile's east, north and gust (m/s) and alpha (0-1) as floats."""
    a = rgba.astype(np.float32)
    return (a[..., 0] - 128) / 4, (a[..., 1] - 128) / 4, a[..., 2] / 5, a[..., 3] / 255


def parent(children):
    """A tile from its four children ({(dx, dy): rgba}), two by two (weighted by their alpha)."""
    east = np.zeros((SIZE * 2, SIZE * 2), np.float32)
    north, gust, alpha = east.copy(), east.copy(), east.copy()
    for (dx, dy), rgba in children.items():
        e, n, g, a = decode(rgba)
        s = (slice(dy * SIZE, dy * SIZE + SIZE), slice(dx * SIZE, dx * SIZE + SIZE))
        east[s], north[s], gust[s], alpha[s] = e * a, n * a, g * a, a

    def half(values):
        return values.reshape(SIZE, 2, SIZE, 2).sum(axis=(1, 3))
    weight = half(alpha)
    safe = np.maximum(weight, 1e-6)
    rgb = tiler.wind_rgb(half(east) / safe, half(north) / safe, half(gust) / safe)
    return np.concatenate([rgb, np.rint(np.clip(weight / 4, 0, 1) * 255)[..., None]], axis=-1).astype(np.uint8)


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    started = time.time()
    kind_total = mt.KIND_TOTAL
    models = []
    for name, label, km, coast_km in WORLD_MODELS:
        try:
            models.append(mt.Model(name, label, 1 / km / kind_total, coast_km))
        except Exception as error:
            print(f"{label}: no index ({error})", flush=True)
    if not any(m.name in ("ecmwf", "gfs") and m.times for m in models):
        raise SystemExit("No world model to mix")
    inputs = {m.name: m.files[m.times[0]].split("/")[0] for m in models if m.times}
    if local is None and "--force" not in args:
        before, made = published_index()
        if before == inputs:
            print("World mix: its models' runs are published already")
            return
        if set(before) == set(inputs) and time.time() - made < EVERY:
            print(f"World mix: made {(time.time() - made) / 3600:.1f} h ago, the next after {EVERY // 3600} h")
            return
    # Every model counts where it has data, the global ones too (mix_tiles fades where only they are).
    mt.GLOBAL = set()
    # Hourly to 5 days (GFS, GDPS, and ICON and ICON-EU to 78 hours are hourly; the owner, 18.10.2026:
    # the models' full resolution), 3-hourly to 6 days, then 6-hourly (UTC 00, 06, 12, 18).
    now = int(time.time()) // 3600 * 3600
    last = min(max(m.times[-1] for m in models if m.times), now + DAYS * 86400)
    hours = [t for t in range(now, last + 1, 3600)
             if t - now < 5 * 86400 or (t - now < 6 * 86400 and t % (3 * 3600) == 0) or t % (6 * 3600) == 0]
    if limit:
        hours = hours[:limit]
    run_time = datetime.fromtimestamp(max(m.run for m in models if m.run), timezone.utc)
    out = tiler.Run("mix-world", run_time, ZOOMS, "World mix", CREDIT, True, local)
    coast = mt.Coast()
    n = 2 ** Z
    keys = [(Z, x, y) for x in range(n) for y in range(n)]
    print(f"World mix {out.folder}: {len(hours)} hours, models: " + ", ".join(f"{m.label} {inputs.get(m.name)}" for m in models), flush=True)
    for t in hours:
        when = {m.name: m.around(t) for m in models}
        needed = [(m, ht) for m in models for ht, _ in when[m.name] if ht not in m.packs]
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda job: job[0].pack(job[1]), needed))
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda key: (key, mt.mix_tile(models, when, coast, *key)), keys))
        level, used, checks = {}, set(), ([], [], [])
        for key, result in results:
            if result is None:
                continue
            rgba, names, (ue, un, g) = result
            level[key[1:]] = rgba
            used.update(names)
            for i, arr in enumerate((ue, un, g)):
                if arr.size:
                    checks[i].append(arr[::7])
        for m in models:
            m.forget(min((ht for ht, _ in when[m.name]), default=t))
        if not level:
            continue
        tiles = {(Z, x, y): rgba for (x, y), rgba in level.items()}
        for z in range(Z - 1, -1, -1):
            upper = {}
            for x in range(2 ** z):
                for y in range(2 ** z):
                    children = {(dx, dy): level[(2 * x + dx, 2 * y + dy)] for dx in (0, 1) for dy in (0, 1)
                                if (2 * x + dx, 2 * y + dy) in level}
                    if children:
                        upper[(x, y)] = parent(children)
            level = upper
            tiles.update({(z, x, y): rgba for (x, y), rgba in level.items()})
        east, north, gust = (np.concatenate(c) if c else np.zeros(0, np.float32) for c in checks)
        if not out.checked("World mix", t, quality.wind(east, north, gust)):
            continue
        out.put(t, tiler.world_tiles.encode({k: tiler.world_tiles.png(v, "RGBA") for k, v in tiles.items()},
                                            {z: SIZE for z in ZOOMS}))
        out.frames[t]["models"] = [m.label for m in models if m.label in used]
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%d %H:%M}: {len(tiles)} tiles, "
              f"{', '.join(out.frames[t]['models'])}, {time.time() - started:.0f} s", flush=True)
    out.finish(weights={m.label: m.weight for m in models}, inputs=inputs)
    print(f"World mix done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up: their exit can crash Python after the work is published
    # ("double free or corruption", 18.10.2026), failing the run and the workflow's next step.
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
