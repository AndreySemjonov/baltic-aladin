"""Europe's fine waves as one tile set (the app's docs/WAVES_PLAN.md, W2; the owner, 14.10.2026):
the Copernicus Marine regional wave models and DWD's EWAM blended into tiles/waves, at each place
the finest model that reaches it, fading into the next over its area's edge (as the Mix's models):

1. Baltic (BAL WAV, WAM, 1 nautical mile)
2. North-West Shelf (NWS WAV, about 1.5 km): the North Sea, the Channel, the UK and Ireland
3. Iberia-Biscay-Ireland (IBI WAV, about 3 km): Portugal, Spain's Atlantic, Biscay
4. Black Sea (BS WAV, about 2.5 km)
5. Mediterranean (MED WAV, about 4.6 km)
6. EWAM (DWD, about 5 km, 78 hours) under them all

Hourly for 2 days, every 3 hours to 6 days and every 6 to 10, as far as each model goes; zooms
3-7. The app draws ECMWF's world waves (tiles/ecmwf-waves) under it and after it. The height,
the period (mean, TM10) and the direction it comes from, as waves_tiles.py; the weight as alpha.
The hours are made in 4 processes, each reading its own hours of each model.

Free data, but Copernicus needs our account (COPERNICUSMARINE_SERVICE_USERNAME and _PASSWORD,
GitHub secrets). Source: E.U. Copernicus Marine Service Information; DWD EWAM (CC BY 4.0).

    python waves_composite.py [--local DIR] [--hours N]
"""
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

import quality
import tiler
import wave_map
import waves_tiles
import world_tiles
from waves_tiles import direction_parts, filled, regular_grid

# Finest first (BAL before NWS in the Kattegat: the Baltic's own model, as the Baltic picture).
MODELS = [
    ("BAL", "cmems_mod_bal_wav_anfc_PT1H-i"),
    ("NWS", "cmems_mod_nws_wav_anfc_1.5km_PT1H-i"),
    ("IBI", "cmems_mod_ibi_wav_anfc_0.027deg_PT1H-i"),
    ("BS", "cmems_mod_blk_wav_anfc_2.5km_PT1H-i"),
    ("MED", "cmems_mod_med_wav_anfc_4.2km_PT1H-i"),
]
VARIABLES = ("VHM0", "VTM10", "VMDR")
ZOOMS = range(3, 8)
FADE_KM = 40
STEPS = list(range(0, 48)) + list(range(48, 145, 3)) + list(range(150, 241, 6))
BLOCK = 6
CREDIT = "E.U. Copernicus Marine Service Information (BAL, NWS, IBI, BS, MED wave forecasts); Deutscher Wetterdienst (DWD), EWAM (CC BY 4.0)"


class Grid:
    """One model's hour: its fields (land filled 3 cells out), where it lies and how much it counts."""

    def __init__(self, name, box, place, sample, height, period, direction):
        self.name, self.box, self.place, self.sample = name, box, place, sample
        self.height, self.period = height, period
        self.east, self.north = direction_parts(direction)

    def touches(self, lon, lat):
        south, north, west, east = self.box
        return lat.max() >= south and lat.min() <= north and lon.max() >= west and lon.min() <= east

    def at(self, lon, lat):
        """Height, period, direction parts and weight at the pixels (weight 0 where it has none)."""
        col, row, inside = self.place(lon, lat)
        weight = inside * tiler.edge_weight(lon, lat, self.box, FADE_KM)
        if not np.any(weight > 0):
            return None
        h = self.sample(self.height, col, row)
        weight = np.where(np.isfinite(h) & (weight > 0), weight, 0).astype(np.float32)
        if not np.any(weight > 0):
            return None
        return (np.nan_to_num(h), np.nan_to_num(self.sample(self.period, col, row)),
                np.nan_to_num(self.sample(self.east, col, row)), np.nan_to_num(self.sample(self.north, col, row)), weight)


def cmems_grid(name, ds, index):
    """A Copernicus model's hour `index` of `ds` (loaded) as a Grid."""
    lats, lons = ds["latitude"].values.astype(np.float64), ds["longitude"].values.astype(np.float64)
    fields = {v: ds[v].values[index].astype(np.float32) for v in VARIABLES}
    missing = ~np.isfinite(fields["VHM0"])
    place, sample = regular_grid(lats[0], lons[0], lats[1] - lats[0], lons[1] - lons[0], len(lats), len(lons), False)
    box = (float(lats.min()), float(lats.max()), float(lons.min()), float(lons.max()))
    h, p, d = (filled(fields[v], missing) for v in VARIABLES)
    return Grid(name, box, place, sample, h, p, d), np.where(missing, np.nan, fields["VHM0"]), np.where(missing, np.nan, fields["VTM10"])


def ewam_grid(run, step):
    """EWAM's hour as a Grid (None when it can't be read)."""
    fields, missing, grid = {}, None, None
    for variable in wave_map.VARIABLES:
        try:
            data = wave_map.download(run, step, variable)
        except Exception as error:
            print(f"EWAM {variable} +{step} h: {error}", flush=True)
            return None
        fields[variable], m, grid = waves_tiles.decode_masked(data)
        missing = m if missing is None else missing | m
    lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
    lon0 = lon0 - 360 if lon0 > 180 else lon0
    dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
    dlon, nj, ni = grid["iDirectionIncrementInDegrees"], grid["Nj"], grid["Ni"]
    place, sample = regular_grid(lat0, lon0, dlat, dlon, nj, ni, False)
    box = (min(lat0, lat0 + dlat * (nj - 1)), max(lat0, lat0 + dlat * (nj - 1)), lon0, lon0 + dlon * (ni - 1))
    h, p, d = (filled(fields[name], missing) for name in ("SWH", "TM10", "MWD"))
    return Grid("EWAM", box, place, sample, h, p, d), np.where(missing, np.nan, fields["SWH"]), np.where(missing, np.nan, fields["TM10"])


def composite(grids, keys):
    """The hour's pack: each tile blended from the grids in order (each over the ones after it by
    its weight); tiles with nothing left out."""
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
        if not np.any(total > 0.004):
            continue
        safe = np.maximum(total, 1e-6)
        height = np.where(total > 0.004, acc[0] / safe, np.nan)
        direction = (np.degrees(np.arctan2(acc[2], acc[3])) + 360) % 360
        rgb = tiler.waves_rgb(height, acc[1] / safe, direction)
        alpha = np.where(total > 0.004, np.clip(np.rint(total * 255), 1, 255), 0)
        tiles[key] = world_tiles.png(np.concatenate([rgb, alpha[..., None]], axis=-1).astype(np.uint8), "RGBA")
    return world_tiles.encode(tiles, {z: tiler.SIZE for z in ZOOMS})


def make_block(job):
    """Some hours (Unix times) in one process: (time, pack or None, [(model, reason)]) each."""
    times, keys, have, ewam_run = job
    import copernicusmarine
    loaded = {}
    for name, dataset in MODELS:
        wanted = [t for t in times if t in have.get(name, ())]
        if not wanted:
            continue
        try:
            ds = copernicusmarine.open_dataset(dataset_id=dataset, variables=list(VARIABLES), disable_progress_bar=True)
            stamps = ds["time"].values.astype("datetime64[s]").astype(np.int64)
            index = [int(np.nonzero(stamps == t)[0][0]) for t in wanted]
            loaded[name] = (ds.isel(time=index).load(), wanted)
        except Exception as error:
            print(f"{name}: hours {wanted[0]}-{wanted[-1]} not read ({error})", flush=True)
    out = []
    ewam_time = int(datetime.strptime(ewam_run, "%Y%m%d%H").replace(tzinfo=timezone.utc).timestamp()) if ewam_run else None
    for t in times:
        grids, failures = [], []
        for name, _ in MODELS:
            if name not in loaded or t not in loaded[name][1]:
                continue
            ds, wanted = loaded[name]
            grid, height, period = cmems_grid(name, ds, wanted.index(t))
            reason = quality.waves(height, period)
            if reason:
                failures.append((name, reason))
                continue
            grids.append(grid)
        if ewam_time is not None and (t - ewam_time) // 3600 in waves_tiles.EWAM_STEPS and (t - ewam_time) % 3600 == 0:
            got = ewam_grid(ewam_run, (t - ewam_time) // 3600)
            if got:
                grid, height, period = got
                reason = quality.waves(height, period)
                if reason:
                    failures.append(("EWAM", reason))
                else:
                    grids.append(grid)
        out.append((t, composite(grids, keys) if grids else None, [g.name for g in grids], failures))
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%d.%m %H} UTC: {', '.join(g.name for g in grids) or 'nothing'}", flush=True)
    return out


def sea_tiles(have, first):
    """The tiles at zooms 3-7 with sea in any model (from each one's first hour)."""
    import copernicusmarine
    grids = []
    for name, dataset in MODELS:
        if name not in have:
            continue
        ds = copernicusmarine.open_dataset(dataset_id=dataset, variables=list(VARIABLES), disable_progress_bar=True)
        stamps = ds["time"].values.astype("datetime64[s]").astype(np.int64)
        t = min(have[name])
        grids.append(cmems_grid(name, ds.isel(time=[int(np.nonzero(stamps == t)[0][0])]).load(), 0)[0])
    if first:
        grids.append(first)
    keys = set()
    for grid in grids:
        for key in tiler.tiles_in_box(grid.box, ZOOMS):
            if key in keys:
                continue
            lon, lat = tiler.tile_lonlat(*key)
            if grid.at(lon, lat) is not None:
                keys.add(key)
    return sorted(keys)


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    import copernicusmarine
    started = time.time()
    run_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    targets = [int((run_time + timedelta(hours=s)).timestamp()) for s in (STEPS[:limit] if limit else STEPS)]
    # Which of the hours each model has (its times only: no values read here).
    have = {}
    for name, dataset in MODELS:
        try:
            ds = copernicusmarine.open_dataset(dataset_id=dataset, variables=["VHM0"], disable_progress_bar=True)
            stamps = set(int(s) for s in ds["time"].values.astype("datetime64[s]").astype(np.int64))
            have[name] = [t for t in targets if t in stamps]
            print(f"{name}: {len(have[name])} of {len(targets)} hours", flush=True)
        except Exception as error:
            print(f"{name}: not read ({error})", flush=True)
    try:
        ewam_run = wave_map.newest_run()
    except SystemExit as error:
        print(f"EWAM: {error}", flush=True)
        ewam_run = None
    first = None
    if ewam_run:
        ewam_time = datetime.strptime(ewam_run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        step = max(0, int((run_time - ewam_time).total_seconds() // 3600))
        got = ewam_grid(ewam_run, step) if step in waves_tiles.EWAM_STEPS else None
        first = got[0] if got else None
    if not have and not first:
        raise SystemExit("no wave model could be read")
    keys = sea_tiles(have, first)
    print(f"{len(keys)} tiles with sea, {time.time() - started:.0f} s", flush=True)
    out = tiler.Run("waves", run_time, ZOOMS, "Copernicus seas, EWAM", CREDIT, True, local)
    blocks = [targets[i:i + BLOCK] for i in range(0, len(targets), BLOCK)]
    jobs = [(block, keys, {n: set(h) for n, h in have.items()}, ewam_run) for block in blocks]
    with ProcessPoolExecutor(4) as pool:
        for results in pool.map(make_block, jobs):
            for t, data, models, failures in results:
                for name, reason in failures:
                    out.report.fail(name, t, reason)
                out.report.count()
                if data is None:
                    continue
                out.put(t, data)
            print(f"  {len(out.frames)} hours, {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    if not out.frames:
        raise SystemExit("no hours made")
    out.finish()


if __name__ == "__main__":
    main()
