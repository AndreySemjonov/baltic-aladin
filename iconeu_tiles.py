"""ICON-EU over all of Europe as tiles (docs/TILES_PLAN.md in the app's repository, phase 2; the
owner, 12.10.2026: the step between the regional models' two days and ECMWF's 25 km, everywhere in
Europe). DWD's 7 km model (open data, CC BY 4.0): 00/06/12/18 UTC runs to 120 hours, its whole
area (about 29.5-70.5N, 23.5W-62.5E) at zooms 3-5 (about 2.7 km a pixel at 50N, finer than its
grid), hourly to 24 hours, then every 3 hours (about 3.7 MB of wind and 5.5 of air and rain an
hour: 57 hours a run, about 0.5 GB). Two sets through the shared tiler:
- tiles/icon-eu: the wind (east, north, the hour's strongest gust);
- tiles/icon-eu-wx: the air (air, feels like, sea-level pressure) and the rain (the hour's, from
  the run's total so far; every 3 hours its average) with the cloud cover.
- tiles/icon-eu-steer: the wind at 700 hPa (about 3 km up), which carries the showers, at zoom 3
  only (about 12 km a pixel; a few hundred KB a run): the app moves the forecast's rain along it
  between the hours (the owner, 13.10.2026: matched hour to hour, the showers went down, sideways,
  down again).
Its weight fades over the last 150 km to its area's edge.

    python iconeu_tiles.py [--force] [--local DIR] [--hours N]
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

import iconeu_map
import quality
import temp_map
import tiler

ZOOMS = range(3, 6)
# Hourly while ICON-EU is (78 hours: the spots' hours come from these tiles; the owner, 18.10.2026),
# then every 3 hours.
STEPS = list(range(0, 79)) + list(range(81, 121, 3))
FADE_KM = 150
WIND = ("U_10M", "V_10M", "VMAX_10M")
# The clouds low (under about 2 km), mid (2-6 km) and high (over 6 km) too: the app's forecast clouds in
# layers (the owner, 15.10.2026).
WEATHER = ("U_10M", "V_10M", "T_2M", "TD_2M", "PMSL", "TOT_PREC", "CLCT", "CLCL", "CLCM", "CLCH")
# The wind aloft: (name in the fields, variable, level in hPa).
STEER = (("U_700", "U", 700), ("V_700", "V", 700))
STEER_ZOOMS = range(3, 4)
CREDIT = "ICON-EU, DWD (CC BY 4.0)"


def fetch(run, step, variable):
    name, level = variable, None
    for key, var, hpa in STEER:
        if key == variable:
            name, level = var, hpa
    try:
        return iconeu_map.download(run, step, name, level)
    except Exception as error:
        print(f"ICON-EU {variable} +{step} h: {error}", flush=True)
        return None


def grid_tools(grid, shape, zooms=ZOOMS):
    """The model grid's placement on the tiles (its area, fading at the edge) and its sampler."""
    nj, ni = shape
    lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
    if lon0 > 180:
        lon0 -= 360
    dlon = grid["iDirectionIncrementInDegrees"]
    dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
    lat1 = lat0 + dlat * (nj - 1)
    box = (min(lat0, lat1), max(lat0, lat1), lon0, lon0 + dlon * (ni - 1))

    def place(lon, lat):
        col, row = (lon - lon0) / dlon, (lat - lat0) / dlat
        inside = (col >= 0) & (row >= 0) & (col <= ni - 1) & (row <= nj - 1)
        return col, row, np.where(inside, tiler.edge_weight(lon, lat, box, FADE_KM), 0)

    def sample(values, col, row):
        c = np.clip(col, 0, ni - 1.001)
        r = np.clip(row, 0, nj - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        top = values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc
        bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr

    return tiler.Placement(tiler.tiles_in_box(box, zooms), place), sample, box


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    force = "--force" in args
    run = iconeu_map.newest_run()
    run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    wind = tiler.Run("icon-eu", run_time, ZOOMS, "ICON-EU 7 km", CREDIT, force, local and local + "/icon-eu")
    weather = tiler.Run("icon-eu-wx", run_time, ZOOMS, "ICON-EU 7 km (air and rain)", CREDIT, force, local and local + "/icon-eu-wx")
    steer = tiler.Run("icon-eu-steer", run_time, STEER_ZOOMS, "ICON-EU 7 km (wind at 700 hPa)", CREDIT, force,
                      local and local + "/icon-eu-steer")
    if not wind.wanted and not weather.wanted and not steer.wanted:
        print(f"ICON-EU {run} already published")
        return
    steps = STEPS[:limit] if limit else STEPS
    variables = sorted(set(WIND if wind.wanted else ()) | set(WEATHER if weather.wanted else ())
                       | set(key for key, _, _ in STEER if steer.wanted))
    start, started = int(run_time.timestamp()), time.time()
    placement = sample = box = steer_placement = None
    # The spots' values from ICON-EU's own cells (spot_cells.py; the owner, 18.10.2026).
    import spot_cells
    try:
        cells = spot_cells.SpotCells("icon-eu", "icon_eu", weather="icon-eu-wx")
    except Exception as error:
        print(f"ICON-EU: the spots' cells left out ({error})", flush=True)
        cells = None
    totals = {}
    for k in range(0, len(steps), 6):
        chunk = steps[k:k + 6]
        jobs = [(step, variable) for step in chunk for variable in variables]
        # Downloads side by side, decoding one at a time (ecCodes isn't thread safe).
        with ThreadPoolExecutor(8) as pool:
            raw = dict(zip(jobs, pool.map(lambda job: fetch(run, *job), jobs)))
        for step in chunk:
            fields = {}
            for variable in variables:
                data = raw.pop((step, variable), None)
                if data is not None:
                    grid, fields[variable] = iconeu_map.decode(data)
            if not fields:
                continue
            if placement is None:
                placement, sample, box = grid_tools(grid, next(iter(fields.values())).shape)
                steer_placement = grid_tools(grid, next(iter(fields.values())).shape, STEER_ZOOMS)[0]
                print(f"ICON-EU {run}: {len(placement.parts)} tiles over {box}", flush=True)
            t = start + step * 3600
            east, north = fields.get("U_700"), fields.get("V_700")
            if steer.wanted and east is not None and north is not None:
                if steer.checked("ICON-EU", t, quality.steering(east, north)):
                    steer.put(t, tiler.pack(steer_placement, sample, lambda at: tiler.steer_rgb(at(east), at(north))))
            u, v = fields.get("U_10M"), fields.get("V_10M")
            if wind.wanted and u is not None and v is not None:
                gust = fields.get("VMAX_10M", np.hypot(u, v))
                if wind.checked("ICON-EU", t, quality.wind(u, v, gust)):
                    wind.put(t, tiler.pack(placement, sample, lambda at: tiler.wind_rgb(at(u), at(v), at(gust))))
                if cells is not None:
                    cells.add_regular(t, grid, {"10u": u, "10v": v, "gust": gust})
            if not weather.wanted:
                continue
            if "TOT_PREC" in fields:
                totals[step] = fields["TOT_PREC"]
            before = max((s for s in totals if s < step), default=None)
            if step == 0 or before is None or step not in totals or u is None or v is None \
                    or not {"T_2M", "TD_2M", "CLCT"} <= fields.keys():
                continue
            air = fields["T_2M"] - 273.15
            dew = fields["TD_2M"] - 273.15
            humidity = np.clip(np.exp(17.625 * dew / (243.04 + dew)) / np.exp(17.625 * air / (243.04 + air)), 0, 1)
            feels = temp_map.feels_like(air, np.hypot(u, v), humidity)
            hpa = fields["PMSL"] / 100 if "PMSL" in fields else np.full(air.shape, np.nan, dtype=np.float32)
            rain = np.maximum(totals[step] - totals[before], 0) / (step - before)
            cloud = fields["CLCT"] / 100
            if not weather.checked("ICON-EU", t, quality.weather(air, rain)):
                continue
            if cells is not None:
                layers = {name: fields[key] for name, key in (("low", "CLCL"), ("mid", "CLCM"), ("high", "CLCH")) if key in fields}
                cells.add_regular(t, grid, {"10u": u, "10v": v, "gust": fields.get("VMAX_10M", np.hypot(u, v)),
                                            "2t": fields["T_2M"], "tcc": fields["CLCT"], **layers}, rain)
            weather.put(t, tiler.pack(placement, sample, lambda at: tiler.temp_rgb(at(air), at(feels), at(hpa))), "temp")
            weather.put(t, tiler.pack(placement, sample, lambda at: tiler.rain_rgb(at(rain), at(cloud))), "rain")
            if {"CLCL", "CLCM", "CLCH"} <= fields.keys():
                low, mid, high = (fields[name] / 100 for name in ("CLCL", "CLCM", "CLCH"))
                weather.put(t, tiler.pack(placement, sample, lambda at: tiler.cloud_rgb(at(low), at(mid), at(high))), "cloud")
            # The totals before the one before aren't needed any more.
            for s in [s for s in totals if s < before]:
                del totals[s]
        print(f"  +{chunk[-1]} h, {time.time() - started:.0f} s", flush=True)
    if box is None:
        raise SystemExit(f"ICON-EU {run}: nothing read")
    area = {"south": box[0], "north": box[1], "west": box[2], "east": box[3]}
    if wind.wanted and wind.frames:
        wind.finish(area=area)
    if weather.wanted and weather.frames:
        weather.finish(area=area)
    if steer.wanted and steer.frames:
        steer.finish(area=area)
    if cells is not None:
        try:
            cells.publish((wind if wind.wanted else weather).folder, local and local + "/points")
        except Exception as error:
            print(f"ICON-EU: the spots' cells not published ({error})", flush=True)
    print(f"ICON-EU {run}: done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
