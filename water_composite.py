"""Europe's seas as one tile set: the sea level, the water temperature and the surface current (the
app's docs/WATER_TILES_PLAN.md, W1; the owner, 15.10.2026: the water map beyond the Baltic picture),
the Copernicus Marine physics forecasts blended into tiles/sea, at each place the finest model that
reaches it, fading into the next over its area's edge (as the waves composite):

0. The Baltic water picture (W2: NEMO-EST at about 1 km in Estonia and the Gulf of Riga, the
   Copernicus Baltic model round it, shifted and nudged to the Estonian and Latvian gauges: the
   helper's water_map.py, published every run as water/ on its site), over its own box
1. Baltic (BAL PHY, about 1.7 km), its level shifted onto the gauges' height (EH2000) as the
   picture shifts it (its "copernicusOffset"), so the Baltic has one level with no step
2. North-West Shelf (NWS PHY, 1.5 km): the North Sea, the Channel, the UK and Ireland
3. Iberia-Biscay-Ireland (IBI PHY, about 3 km)
4. Black Sea (BS PHY, 2.5 km)
5. Mediterranean (MED PHY, 4.2 km)
6. Global (GLO PHY, about 9 km) under them all, over Europe's box

From 12 hours back, every hour for 3 days, then every 3 hours to 6, as far as each model goes; zooms 3-6 (the models
are 1.5 km and coarser: zoom 7 only repeated their pixels, at four times the size).

The level: the Baltic model's as it gives it (its sea level against the long-term mean, as the
Baltic picture). The others give theirs against their own reference, with a fixed offset there
(the Mediterranean about -40 cm, the Norwegian Sea -65 on a calm day), so each one's mean over its
first day (two tides) is taken off at each place: the tide and the surge against the sea's usual
level, which is what a rider needs.

Each tile RGBA, read as raw numbers:
    red   = the level: 128 + 4.8 x sign x sqrt(|cm|), 1-255 (0 = no sea)
    green = the water temperature: (degrees C + 4) x 7
    blue  = the current's east part: 128 + m/s x 42 (1-255)
    alpha = its north part, the same
The Baltic's sea ice beside it while there is any (<time>-ice.pack): red = 1 + cover x 200 (0 = no
model), green = thickness cm.

Free data, but Copernicus needs our account (COPERNICUSMARINE_SERVICE_USERNAME and _PASSWORD,
GitHub secrets). Source: E.U. Copernicus Marine Service Information.

    python water_composite.py [--local DIR] [--hours N]
"""
import multiprocessing
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

import tiler
import world_tiles
from waves_tiles import filled, regular_grid

# The Baltic water picture (W2): its frames, decoded here as the app read them.
PICTURE = "https://files.meteogust.com/helper/water/"
USER_AGENT = {"User-Agent": "MeteoGust seas (data.meteogust.com)"}

# Each model's datasets for the level, the temperature and the current (the same dataset may serve
# several); finest first.
BAL = "cmems_mod_bal_phy_anfc_PT1H-i"
IBI = "cmems_mod_ibi_phy_anfc_0.027deg-2D_PT1H-m"
GLO = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m"
MODELS = [
    ("BAL", {"level": (BAL, "sla"), "temp": (BAL, "thetao"), "u": (BAL, "uo"), "v": (BAL, "vo")}),
    ("NWS", {"level": ("cmems_mod_nws_phy-ssh_anfc_1.5km-2D_PT1H-i", "zos"),
             "temp": ("cmems_mod_nws_phy-sst_anfc_1.5km-2D_PT1H-i", "thetao"),
             "u": ("cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i", "uo"),
             "v": ("cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i", "vo")}),
    ("IBI", {"level": (IBI, "zos"), "temp": (IBI, "thetao"), "u": (IBI, "uo"), "v": (IBI, "vo")}),
    ("BS", {"level": ("cmems_mod_blk_phy-ssh_anfc_2.5km_PT1H-m", "zos"),
            "temp": ("cmems_mod_blk_phy-temp_anfc_2.5km_PT1H-m", "thetao"),
            "u": ("cmems_mod_blk_phy-cur_anfc_2.5km_PT1H-m", "uo"),
            "v": ("cmems_mod_blk_phy-cur_anfc_2.5km_PT1H-m", "vo")}),
    ("MED", {"level": ("cmems_mod_med_phy-ssh_anfc_4.2km-2D_PT1H-m", "zos"),
             "temp": ("cmems_mod_med_phy-tem_anfc_4.2km-2D_PT1H-m", "thetao"),
             "u": ("cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m", "uo"),
             "v": ("cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m", "vo")}),
    ("GLO", {"level": (GLO, "zos"), "temp": (GLO, "thetao"), "u": (GLO, "uo"), "v": (GLO, "vo")}),
]
FIELDS = ("level", "temp", "u", "v")
ICE = ("siconc", "sithick")
# Europe's box (south, north, west, east): the global model is read only here.
EUROPE = (30.0, 72.0, -30.0, 45.0)
ZOOMS = range(3, 7)
FADE_KM = 40
# From 12 hours back (the models' analysis: Drift starts where a thing was lost, the map shows the
# morning), every hour for 3 days ahead, then every 3 hours to 6.
STEPS = list(range(-12, 72)) + list(range(72, 145, 3))
BLOCK = 4
# A model's hour counts for a time when it is within this of it (the hourly means are stamped at
# the half hour).
NEAR = 2700
CREDIT = "E.U. Copernicus Marine Service Information (BAL, NWS, IBI, BS, MED, GLO physics forecasts)"


def open_dataset(dataset, variables, box=None, span=None):
    """A Copernicus dataset, its surface only (asked by depth where it has one); `box`: only there;
    `span` (first and last Unix time): only those hours (15.10.2026: a whole run's North Sea in
    three processes at once ran the helper out of memory)."""
    import copernicusmarine
    extra = {}
    if box:
        south, north, west, east = box
        extra = {"minimum_latitude": south, "maximum_latitude": north, "minimum_longitude": west, "maximum_longitude": east}
    if span:
        def stamp(t):
            return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        extra.update(start_datetime=stamp(span[0] - NEAR), end_datetime=stamp(span[1] + NEAR))
    try:
        ds = copernicusmarine.open_dataset(dataset_id=dataset, variables=variables, service="arco-geo-series",
                                           minimum_depth=0, maximum_depth=3, **extra)
    except Exception:
        ds = copernicusmarine.open_dataset(dataset_id=dataset, variables=variables, service="arco-geo-series", **extra)
    if "depth" in ds.dims:
        ds = ds.isel(depth=0)
    return ds


def stamps_of(ds):
    return ds["time"].values.astype("datetime64[s]").astype(np.int64)


def nearest(stamps, t):
    """The index of the stamp nearest `t` within NEAR, or None."""
    k = int(np.argmin(np.abs(stamps - t)))
    return k if abs(int(stamps[k]) - t) <= NEAR else None


class Grid:
    """One model's hour: each field on its own grid (land filled 3 cells out), where the model lies
    and how much it counts."""

    def __init__(self, name, box, layers):
        # layers: {field: (place, sample, values)}; the level's sets where the model is.
        self.name, self.box, self.layers = name, box, layers

    def touches(self, lon, lat):
        south, north, west, east = self.box
        return lat.max() >= south and lat.min() <= north and lon.max() >= west and lon.min() <= east

    def at(self, lon, lat):
        """Level, temperature, east and north current and the weight at the pixels (weight 0 where
        it has nothing)."""
        place, sample, level = self.layers["level"]
        col, row, inside = place(lon, lat)
        weight = inside * tiler.edge_weight(lon, lat, self.box, FADE_KM)
        if not np.any(weight > 0):
            return None
        h = sample(level, col, row)
        weight = np.where(np.isfinite(h) & (weight > 0), weight, 0).astype(np.float32)
        if not np.any(weight > 0):
            return None
        out = [np.nan_to_num(h)]
        for field in FIELDS[1:]:
            place, sample, values = self.layers[field]
            c, r, _ = place(lon, lat)
            out.append(np.nan_to_num(sample(values, c, r)))
        return (*out, weight)


def layer(ds, variable, index, offset=None):
    """One field's hour as (place, sample, values), its land filled, and the raw values; `offset`
    (on the same grid) taken off."""
    lats, lons = ds["latitude"].values.astype(np.float64), ds["longitude"].values.astype(np.float64)
    raw = ds[variable].values[index].astype(np.float32)
    if offset is not None and (np.ndim(offset) == 0 or np.shape(offset) == raw.shape):
        raw = raw - offset
    missing = ~np.isfinite(raw)
    place, sample = regular_grid(lats[0], lons[0], lats[1] - lats[0], lons[1] - lons[0], len(lats), len(lons), False)
    return (place, sample, filled(raw, missing)), raw, (float(lats.min()), float(lats.max()), float(lons.min()), float(lons.max()))


def check(level, temp):
    """Why an hour looks broken (None: fine): a level or a temperature off the scale, or none."""
    if not np.any(np.isfinite(level)):
        return "no sea level"
    if np.nanmax(np.abs(level)) > 15:
        return f"level {np.nanmax(np.abs(level)):.1f} m"
    if np.any(np.isfinite(temp)) and (np.nanmin(temp) < -5 or np.nanmax(temp) > 40):
        return f"temperature {np.nanmin(temp):.0f} to {np.nanmax(temp):.0f} C"
    return None


def sea_rgba(level, temp, east, north):
    """The tile's RGBA (see the top)."""
    cm = level * 100
    red = np.clip(np.rint(128 + 4.8 * np.sign(cm) * np.sqrt(np.abs(cm))), 1, 255)
    green = np.clip(np.rint((temp + 4) * 7), 0, 255)
    blue = np.clip(np.rint(128 + east * 42), 1, 255)
    alpha = np.clip(np.rint(128 + north * 42), 1, 255)
    return np.stack([red, green, blue, alpha], axis=-1)


def composite(grids, keys):
    """The hour's pack: each tile blended from the grids in order (each over the ones after it by
    its weight); tiles with no sea left out."""
    tiles = {}
    for key in keys:
        lon, lat = tiler.tile_lonlat(*key)
        shape = lon.shape
        acc = [np.zeros(shape, np.float32) for _ in range(4)]
        remaining = np.ones(shape, np.float32)
        for grid in grids:
            if not grid.touches(lon, lat):
                continue
            got = grid.at(lon, lat)
            if got is None:
                continue
            *values, weight = got
            share = weight * remaining
            for i, value in enumerate(values):
                acc[i] += share * value
            remaining *= 1 - weight
            if not np.any(remaining > 0.002):
                break
        total = 1 - remaining
        sea = total > 0.004
        if not np.any(sea):
            continue
        safe = np.maximum(total, 1e-6)
        rgba = sea_rgba(acc[0] / safe, acc[1] / safe, acc[2] / safe, acc[3] / safe)
        rgba[~sea] = 0
        tiles[key] = world_tiles.png(rgba.astype(np.uint8), "RGBA")
    return world_tiles.encode(tiles, {z: tiler.SIZE for z in ZOOMS})


def ice_pack(ds, index, keys):
    """The Baltic's ice at hour `index` (None when there is none)."""
    lats, lons = ds["latitude"].values.astype(np.float64), ds["longitude"].values.astype(np.float64)
    cover = ds["siconc"].values[index].astype(np.float32)
    if not np.any(np.nan_to_num(cover) >= 0.01):
        return None
    thick = ds["sithick"].values[index].astype(np.float32)
    missing = ~np.isfinite(cover)
    place, sample = regular_grid(lats[0], lons[0], lats[1] - lats[0], lons[1] - lons[0], len(lats), len(lons), False)
    cover, thick = filled(cover, missing), filled(thick, missing)
    tiles = {}
    for key in keys:
        lon, lat = tiler.tile_lonlat(*key)
        col, row, inside = place(lon, lat)
        c = sample(cover, col, row)
        known = (inside > 0) & np.isfinite(c)
        if not np.any(known):
            continue
        red = np.where(known, np.clip(np.rint(1 + np.nan_to_num(c) * 200), 1, 201), 0)
        green = np.where(known, np.clip(np.rint(np.nan_to_num(sample(thick, col, row)) * 100), 0, 255), 0)
        tiles[key] = world_tiles.png(np.stack([red, green, np.zeros_like(red)], axis=-1).astype(np.uint8), "RGB")
    return world_tiles.encode(tiles, {z: tiler.SIZE for z in ZOOMS}) if tiles else None


def load_model(name, datasets, wanted, with_ice):
    """A model's hours `wanted` (Unix times): {field: (dataset loaded, its index per wanted hour)};
    the Baltic's ice too. None when it can't be read."""
    by_dataset = {}
    for field, (dataset, variable) in datasets.items():
        by_dataset.setdefault(dataset, []).append((field, variable))
    out = {}
    for dataset, fields in by_dataset.items():
        variables = [v for _, v in fields] + (list(ICE) if with_ice and dataset == BAL else [])
        ds = open_dataset(dataset, variables, EUROPE if name == "GLO" else None, (min(wanted), max(wanted)))
        stamps = stamps_of(ds)
        index = [nearest(stamps, t) for t in wanted]
        picked = sorted(set(k for k in index if k is not None))
        if not picked:
            return None
        loaded = ds.isel(time=picked).load()
        position = {k: i for i, k in enumerate(picked)}
        rows = [position.get(k) if k is not None else None for k in index]
        for field, variable in fields:
            out[field] = (loaded, variable, rows)
        if with_ice and dataset == BAL:
            out["ice"] = (loaded, None, rows)
    return out


def picture_index():
    """The Baltic water picture's run: {frames: {time: file}, bounds, width, height, shift (the
    Copernicus level's to the gauges, m)}; None when it can't be read."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(PICTURE + "map.json", headers=USER_AGENT), timeout=60) as reply:
            manifest = json.load(reply)
        return {"frames": {int(f["time"]): f["file"] for f in manifest["frames"]}, "bounds": manifest["bounds"],
                "width": int(manifest["width"]), "height": int(manifest["height"]),
                "shift": float(manifest.get("copernicusOffset") or 0) / 100}
    except Exception as error:
        print(f"Baltic picture: not read ({error})", flush=True)
        return None


def picture_grid(info, t):
    """The Baltic water picture's hour as a Grid (red level cm + 128, 0 none; green degC x 8; blue
    current m/s x 100; alpha towards degrees x 256/360; Web Mercator over its bounds); None when
    it has no frame then or can't be read."""
    import io
    import urllib.request
    from PIL import Image
    file = info["frames"].get(t)
    if not file:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(PICTURE + file, headers=USER_AGENT), timeout=60) as reply:
            pixels = np.asarray(Image.open(io.BytesIO(reply.read())).convert("RGBA")).astype(np.float32)
    except Exception as error:
        print(f"Baltic picture {t}: not read ({error})", flush=True)
        return None
    b, width, height = info["bounds"], info["width"], info["height"]
    sea = pixels[..., 0] > 0
    level = np.where(sea, (pixels[..., 0] - 128) / 100, np.nan).astype(np.float32)
    temp = np.where(sea, pixels[..., 1] / 8, np.nan).astype(np.float32)
    speed = pixels[..., 2] / 100
    towards = np.radians(pixels[..., 3] * 360 / 256)
    east = np.where(sea, speed * np.sin(towards), np.nan).astype(np.float32)
    north = np.where(sea, speed * np.cos(towards), np.nan).astype(np.float32)

    def mercator(lat):
        return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    top, bottom = mercator(b["north"]), mercator(b["south"])
    _, sample = regular_grid(0, 0, 1, 1, height, width, False)

    def place(lon, lat):
        # Pixel centres: column 0 half a pixel in from the west edge (the image spans the bounds).
        col = (lon - b["west"]) / (b["east"] - b["west"]) * width - 0.5
        row = (top - mercator(np.clip(lat, -85, 85))) / (top - bottom) * height - 0.5
        inside = (col >= 0) & (col <= width - 1) & (row >= 0) & (row <= height - 1)
        return col, row, inside.astype(np.float32)
    layers = {"level": (place, sample, level), "temp": (place, sample, temp), "u": (place, sample, east), "v": (place, sample, north)}
    return Grid("Baltic", (b["south"], b["north"], b["west"], b["east"]), layers)


def make_block(job):
    """Some hours (Unix times) in one process: (time, pack or None, ice pack or None, models,
    [(model, reason)]) each."""
    times, keys, ice_keys, have, means, picture = job
    loaded = {}
    for name, datasets in MODELS:
        wanted = [t for t in times if t in have.get(name, ())]
        if not wanted:
            continue
        began = time.time()
        try:
            got = load_model(name, datasets, wanted, name == "BAL")
        except Exception as error:
            print(f"{name}: hours {wanted[0]}-{wanted[-1]} not read ({error})", flush=True)
            continue
        if got:
            loaded[name] = (got, wanted)
            print(f"{name}: {len(wanted)} hours read in {time.time() - began:.0f} s", flush=True)
    out = []
    for t in times:
        grids, failures = [], []
        ice = None
        # The Baltic picture first, over its box (NEMO-EST, the gauges).
        fine = picture_grid(picture, t) if picture else None
        if fine:
            grids.append(fine)
        for name, _ in MODELS:
            if name not in loaded or t not in loaded[name][1]:
                continue
            got, wanted = loaded[name]
            k = wanted.index(t)
            layers, raw = {}, {}
            box = None
            for field in FIELDS:
                ds, variable, rows = got[field]
                if rows[k] is None:
                    break
                offset = means.get(name) if field == "level" else None
                # The Baltic model's level onto the gauges' height, as the picture's.
                if field == "level" and name == "BAL" and picture:
                    offset = -picture["shift"]
                layers[field], raw[field], field_box = layer(ds, variable, rows[k], offset)
                if field == "level":
                    box = field_box
            if len(layers) < len(FIELDS):
                continue
            reason = check(raw["level"], raw["temp"])
            if reason:
                failures.append((name, reason))
                continue
            grids.append(Grid(name, box, layers))
            if "ice" in got and got["ice"][2][k] is not None:
                try:
                    ice = ice_pack(got["ice"][0], got["ice"][2][k], ice_keys)
                except Exception as error:
                    print(f"ice {t}: {error}", flush=True)
        out.append((t, composite(grids, keys) if grids else None, ice, [g.name for g in grids], failures))
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%d.%m %H} UTC: {', '.join(g.name for g in grids) or 'nothing'}"
              f"{' + ice' if ice else ''}", flush=True)
    return out


def sea_tiles(have, first_hours):
    """The tiles at zooms 3-7 with sea in any model (from each one's first hour), and the Baltic
    model's (for the ice)."""
    keys, ice_keys = set(), []
    for name, datasets in MODELS:
        if name not in have:
            continue
        dataset, variable = datasets["level"]
        ds = open_dataset(dataset, [variable], EUROPE if name == "GLO" else None)
        k = nearest(stamps_of(ds), first_hours[name])
        began = time.time()
        one, _, box = layer(ds.isel(time=[k]).load(), variable, 0)
        grid = Grid(name, box, {"level": one, "temp": one, "u": one, "v": one})
        print(f"{name}: first hour read in {time.time() - began:.0f} s", flush=True)
        for key in tiler.tiles_in_box(box, ZOOMS):
            if key in keys and name != "BAL":
                continue
            lon, lat = tiler.tile_lonlat(*key)
            if grid.at(lon, lat) is not None:
                keys.add(key)
                if name == "BAL":
                    ice_keys.append(key)
    return sorted(keys), ice_keys


def level_means(have):
    """Each model's level averaged over its first day (25 hours: two tides) at each place, to take
    off its fixed offset; not the Baltic's (its own is the sea's level already)."""
    means = {}
    for name, datasets in MODELS:
        if name == "BAL" or name not in have:
            continue
        dataset, variable = datasets["level"]
        began = time.time()
        try:
            ds = open_dataset(dataset, [variable], EUROPE if name == "GLO" else None)
            stamps = stamps_of(ds)
            first = int(stamps[np.argmin(np.abs(stamps - min(have[name])))])
            picked = [k for k, s in enumerate(stamps) if first <= s < first + 25 * 3600]
            values = ds[variable].isel(time=picked).load().values.astype(np.float32)
            means[name] = np.nanmean(values, axis=0).astype(np.float32)
            print(f"{name}: its level's day mean from {len(picked)} hours in {time.time() - began:.0f} s", flush=True)
        except Exception as error:
            print(f"{name}: no day mean ({error}); its level as given", flush=True)
    return means


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    started = time.time()
    run_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # A past hour (a test with --local, e.g. last winter's ice: sea-test.yml): 2026-02-20T00.
    if "--date" in args:
        run_time = datetime.strptime(args[args.index("--date") + 1], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    targets = [int((run_time + timedelta(hours=s)).timestamp()) for s in (STEPS[:limit] if limit else STEPS)]
    # Which of the hours each model has (its level's times only: no values read here).
    have, first_hours = {}, {}
    for name, datasets in MODELS:
        dataset, variable = datasets["level"]
        try:
            stamps = stamps_of(open_dataset(dataset, [variable], EUROPE if name == "GLO" else None))
            have[name] = [t for t in targets if nearest(stamps, t) is not None]
            if have[name]:
                first_hours[name] = have[name][0]
            else:
                del have[name]
            print(f"{name}: {len(have.get(name, []))} of {len(targets)} hours", flush=True)
        except Exception as error:
            print(f"{name}: not read ({error})", flush=True)
    if not have:
        raise SystemExit("no sea model could be read")
    keys, ice_keys = sea_tiles(have, first_hours)
    means = level_means(have)
    picture = picture_index()
    if picture:
        print(f"Baltic picture: {len(picture['frames'])} hours, the Baltic model's level +{picture['shift'] * 100:.1f} cm", flush=True)
    print(f"{len(keys)} tiles with sea ({len(ice_keys)} in the Baltic's), {time.time() - started:.0f} s", flush=True)
    out = tiler.Run("sea", run_time, ZOOMS, "Copernicus seas", CREDIT, True, local)
    blocks = [targets[i:i + BLOCK] for i in range(0, len(targets), BLOCK)]
    jobs = [(block, keys, ice_keys, {n: set(h) for n, h in have.items()}, means, picture) for block in blocks]
    # Fresh processes (spawned): forked after the Copernicus toolbox's threads they hung.
    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
        for results in pool.map(make_block, jobs):
            for t, data, ice, models, failures in results:
                for name, reason in failures:
                    out.report.fail(name, t, reason)
                out.report.count()
                if data is None:
                    continue
                out.put(t, data)
                if ice:
                    out.put(t, ice, "ice")
            print(f"  {len(out.frames)} hours, {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    if not out.frames:
        raise SystemExit("no hours made")
    out.finish(encoding="level 128 + 4.8 sign sqrt|cm| (0 no sea; outside the Baltic against each model's first-day mean), "
                        "(C + 4) x 7, current east and north 128 + m/s x 42; ice: 1 + cover x 200, thickness cm")


if __name__ == "__main__":
    main()
