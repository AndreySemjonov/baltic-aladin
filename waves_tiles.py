"""Waves as tiles (the app's docs/WAVES_PLAN.md, W1; the owner, 14.10.2026: the waves map was the
Baltic's only): the significant wave height, the period and the direction it comes from, through the
shared tiler (tiler.waves_rgb) with the data checks (quality.waves):

- `ecmwf`: ECMWF's wave model for the whole world (open data, the `wave` stream, CC BY 4.0), every 3
  hours to 6 days and every 6 to 10: at 0.1 degrees (the owner, 21.10.2026: free from the 20.10.2026
  06Z run) at zooms 0-4, its 0.25 degrees at zooms 0-3 when those aren't there: tiles/ecmwf-waves;
- `ewam`: DWD's European wave model EWAM (open data, CC BY 4.0: 30-66 N, 10.5 W-42 E, about 5 km,
  00 and 12 UTC, hourly for 2 days then every 3 hours to 78) at zooms 3-6, its weight fading over the last 100 km to its
  box's edge: tiles/ewam.

Land cells within 3 cells of the sea take their nearest sea cell's values (as wave_map.py), so the
water at the coast has the model's own sea next to it; further inland nothing (small files). The app
draws the waves over the sea only (its coastline).

    python waves_tiles.py ecmwf|ewam [--force] [--local DIR] [--hours N]
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import eccodes
import numpy as np
from scipy import ndimage

import ecmwf_map
import quality
import tiler
import wave_map

ECMWF_STEPS = list(range(0, 145, 3)) + list(range(150, 241, 6))
ECMWF_PARAMS = ("swh", "mwp", "mwd")
# Hourly for 2 days, then every 3 hours to its 78 (the app goes smoothly between them).
EWAM_STEPS = list(range(0, 48)) + list(range(48, 79, 3))
EWAM_FADE_KM = 100
CREDIT_ECMWF = "ECMWF open data (CC BY 4.0)"
CREDIT_EWAM = "Deutscher Wetterdienst (DWD), EWAM (CC BY 4.0)"


def filled(values, missing, reach=3):
    """Land (missing) cells within `reach` cells of the sea with their nearest sea cell's value (the
    coast's water gets the model's own sea next to it); further inland NaN (nothing: small files)."""
    values = np.asarray(values, dtype=np.float32)
    if not missing.any():
        return values
    if missing.all():
        return np.full(values.shape, np.nan, dtype=np.float32)
    distance, (ri, ci) = ndimage.distance_transform_edt(missing, return_indices=True)
    return np.where(distance <= reach, values[ri, ci], np.nan).astype(np.float32)


def regular_grid(lat0, lon0, dlat, dlon, nj, ni, wrap):
    """The placement and sampler of a regular latitude-longitude grid (row 0 at lat0, going by
    dlat; columns from lon0 by dlon; `wrap`: round the world)."""
    def place(lon, lat):
        col = ((lon - lon0) % 360) / dlon if wrap else (lon - lon0) / dlon
        row = (lat - lat0) / dlat
        inside = (row >= 0) & (row <= nj - 1) & (col >= 0) & (col <= (ni if wrap else ni - 1))
        return col, row, inside.astype(np.float32)

    def sample(values, col, row):
        r = np.clip(row, 0, nj - 1.001)
        r0 = np.floor(r).astype(int)
        fr = r - r0
        if wrap:
            c0 = np.floor(col).astype(int) % ni
            c1 = (c0 + 1) % ni
            fc = col - np.floor(col)
        else:
            c = np.clip(col, 0, ni - 1.001)
            c0 = np.floor(c).astype(int)
            c1 = c0 + 1
            fc = c - c0
        top = values[r0, c0] * (1 - fc) + values[r0, c1] * fc
        bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c1] * fc
        return top * (1 - fr) + bottom * fr
    return place, sample


def direction_parts(degrees):
    """A direction as its east and north parts (to blend it between cells without the 350/10 jump)."""
    rad = np.radians(degrees)
    return np.sin(rad), np.cos(rad)


def encode_hour(placement, sample, height, period, direction):
    """One hour's pack; the direction sampled through its parts."""
    de, dn = direction_parts(direction)

    def encode(at):
        d = (np.degrees(np.arctan2(at(de), at(dn))) + 360) % 360
        return tiler.waves_rgb(at(height), at(period), d)
    return tiler.pack(placement, sample, encode, valid=lambda at: np.isfinite(at(height)))


# MARK: ECMWF

def ecmwf_url(run, step, suffix, res="0p1"):
    """A step's file or index: "0p1" the 9 km files ("-sfc" in their name), "0p25" the older ones."""
    name = "wave-fc-sfc" if res == "0p1" else "wave-fc"
    return (f"{ecmwf_map.BASE}/{run.strftime('%Y%m%d')}/{run.strftime('%H')}z/ifs/{res}/wave/"
            f"{run.strftime('%Y%m%d%H')}0000-{step}h-{name}.{suffix}")


def ecmwf_newest_run():
    """The newest complete run and its resolution: the 0.1° files first (2 hours later than the 0.25°
    ones), the 0.25° when a newer run has only those."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 12 * 12, minute=0, second=0, microsecond=0)
    for back in range(4):
        run = start - timedelta(hours=12 * back)
        for res in ("0p1", "0p25"):
            try:
                ecmwf_map.get(ecmwf_url(run, ECMWF_STEPS[-1], "index", res))
                return run, res
            except Exception:
                continue
    raise SystemExit("No complete ECMWF wave run found")


def ecmwf_fetch(run, step, res):
    entries = [json.loads(line) for line in ecmwf_map.get(ecmwf_url(run, step, "index", res)).decode().splitlines() if line.strip()]
    messages = {}
    for entry in entries:
        if entry.get("param") in ECMWF_PARAMS:
            start, length = entry["_offset"], entry["_length"]
            messages[entry["param"]] = ecmwf_map.get(ecmwf_url(run, step, "grib2", res), start, start + length - 1)
    return step, messages


def decode_masked(message):
    """A GRIB message's values with its missing (land) cells, and its grid."""
    gid = eccodes.codes_new_from_message(message)
    try:
        grid = {key: eccodes.codes_get(gid, key) for key in (
            "Ni", "Nj", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
            "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees", "jScansPositively")}
        missing_value = eccodes.codes_get(gid, "missingValue")
        values = eccodes.codes_get_values(gid).reshape(grid["Nj"], grid["Ni"]).astype(np.float32)
    finally:
        eccodes.codes_release(gid)
    missing = ~np.isfinite(values) | (np.abs(values - missing_value) < 1e-3) | (values > 9000)
    return values, missing, grid


def run_ecmwf(args):
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    run, res = ecmwf_newest_run()
    # The 0.25° (28 km) files come about 1 h 15 after Open-Meteo's copy of the run, the 0.1° (9 km) about 2 h after
    # those: a run published from the 0.25° is made again from the 0.1° when they come (the owner, 25.10.2026: "publish
    # the 28 km waves as soon as they're out, then replace them with the 9 km ones"; the runner looks every 30 min).
    upgrade = False
    if res == "0p1" and not local and "--force" not in args:
        import world_tiles
        index = world_tiles.published_index("ecmwf-waves") or {}
        upgrade = (index.get("runName") or "").split("-")[0] == f"{run:%Y%m%dT%HZ}" and "9 km" not in (index.get("model") or "")
        if upgrade:
            print(f"ECMWF waves {run:%Y%m%dT%HZ}: published from 0.25°, the 9 km files are out: made again")
    # 0.1°: zoom 4 too (4096 pixels round the world: about its own detail); 0.25°: zooms 0-3.
    zooms = range(0, 5) if res == "0p1" else range(0, 4)
    out = tiler.Run("ecmwf-waves", run, zooms, "ECMWF waves 9 km" if res == "0p1" else "ECMWF waves 0.25°",
                    CREDIT_ECMWF, "--force" in args or upgrade, local)
    if not out.wanted:
        print(f"ECMWF waves {run:%Y%m%dT%HZ} already published" + ("" if res == "0p1" else " (0.25°; the 9 km files not out yet)"))
        return
    steps = ECMWF_STEPS[:limit] if limit else ECMWF_STEPS
    started = time.time()
    placement = sample = None
    for k in range(0, len(steps), 8):
        chunk = steps[k:k + 8]
        with ThreadPoolExecutor(4) as pool:
            got = dict(pool.map(lambda s: ecmwf_fetch(run, s, res), chunk))
        for step in chunk:
            messages = got.get(step) or {}
            if set(messages) != set(ECMWF_PARAMS):
                print(f"  +{step} h: not all fields", flush=True)
                continue
            fields, missing, grid = {}, None, None
            for param in ECMWF_PARAMS:
                fields[param], m, grid = decode_masked(messages[param])
                missing = m if missing is None else missing | m
            if placement is None:
                lat0 = grid["latitudeOfFirstGridPointInDegrees"]
                lon0 = grid["longitudeOfFirstGridPointInDegrees"]
                dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
                place, sample = regular_grid(lat0, lon0, dlat, grid["iDirectionIncrementInDegrees"], grid["Nj"], grid["Ni"], True)
                placement = tiler.Placement(tiler.tiles_in_box((-85, 85, -180, 180), zooms), place)
            t = int(run.timestamp()) + step * 3600
            height = np.where(missing, np.nan, fields["swh"])
            if not out.checked("ECMWF", t, quality.waves(height, np.where(missing, np.nan, fields["mwp"]))):
                continue
            h, p, d = (filled(fields[name], missing) for name in ("swh", "mwp", "mwd"))
            out.put(t, encode_hour(placement, sample, h, p, d))
        print(f"  +{chunk[-1]} h, {len(out.frames)} hours, {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    if out.frames:
        out.finish()


# MARK: EWAM

def run_ewam(args):
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    run = wave_map.newest_run()
    run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    out = tiler.Run("ewam", run_time, range(3, 7), "EWAM 5 km", CREDIT_EWAM, "--force" in args, local)
    if not out.wanted:
        print(f"EWAM {run} already published")
        return
    steps = EWAM_STEPS[:limit] if limit else EWAM_STEPS
    started = time.time()
    placement = sample = None
    for k in range(0, len(steps), 12):
        chunk = steps[k:k + 12]
        jobs = [(s, v) for s in chunk for v in wave_map.VARIABLES]
        with ThreadPoolExecutor(8) as pool:
            raw = dict(zip(jobs, pool.map(lambda job: _ewam_get(run, *job), jobs)))
        for step in chunk:
            fields, missing, grid = {}, None, None
            for variable in wave_map.VARIABLES:
                data = raw.get((step, variable))
                if data is None:
                    break
                fields[variable], m, grid = decode_masked(data)
                missing = m if missing is None else missing | m
            if len(fields) != len(wave_map.VARIABLES):
                continue
            if placement is None:
                lat0 = grid["latitudeOfFirstGridPointInDegrees"]
                lon0 = grid["longitudeOfFirstGridPointInDegrees"]
                lon0 = lon0 - 360 if lon0 > 180 else lon0
                dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
                dlon = grid["iDirectionIncrementInDegrees"]
                nj, ni = grid["Nj"], grid["Ni"]
                base_place, sample = regular_grid(lat0, lon0, dlat, dlon, nj, ni, False)
                box = (min(lat0, lat0 + dlat * (nj - 1)), max(lat0, lat0 + dlat * (nj - 1)), lon0, lon0 + dlon * (ni - 1))

                def place(lon, lat, base_place=base_place, box=box):
                    col, row, inside = base_place(lon, lat)
                    return col, row, inside * tiler.edge_weight(lon, lat, box, EWAM_FADE_KM)
                placement = tiler.Placement(tiler.tiles_in_box(box, range(3, 7)), place)
                print(f"EWAM {run}: {len(placement.parts)} tiles over {box}", flush=True)
            t = int(run_time.timestamp()) + step * 3600
            height = np.where(missing, np.nan, fields["SWH"])
            if not out.checked("EWAM", t, quality.waves(height, np.where(missing, np.nan, fields["TM10"]))):
                continue
            h, p, d = (filled(fields[name], missing) for name in ("SWH", "TM10", "MWD"))
            out.put(t, encode_hour(placement, sample, h, p, d))
        print(f"  +{chunk[-1]} h, {len(out.frames)} hours, {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    if out.frames:
        out.finish()


def _ewam_get(run, step, variable):
    try:
        return wave_map.download(run, step, variable)
    except Exception as error:
        print(f"EWAM {variable} +{step} h: {error}", flush=True)
        return None


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("ecmwf", "ewam"):
        raise SystemExit("python waves_tiles.py ecmwf|ewam [--force] [--local DIR] [--hours N]")
    (run_ecmwf if args[0] == "ecmwf" else run_ewam)(args[1:])


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up: their exit can crash Python after the work is published
    # ("double free or corruption", 18.10.2026), failing the run and the workflow's next step.
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
