"""MET Nordic's wind at its full 1 km over its whole area as tiles (docs/TILES_PLAN.md in the app's
repository, phase 2; the owner, 12.10.2026: every model at its full area, no detail lost): MET
Norway's hourly run (the Nordic countries and the Baltic), read from the run's own file every grid
cell (the regional blend reads every second), hourly for the first day then every 3 hours, through
the shared tiler into tiles/metnordic-1km at zooms 4-7 (zoom 7: about 0.7 km a pixel here), its
weight fading over the last 50 km to its grid's edge. The fields are read side by side, 8 hours at
a time, and each 8 made into tiles before the next.

With --set weather (docs/RAIN_MAP_FIX.md R2: the rain and temperature maps from tiles only), the air
(air, feels like, sea-level pressure) and the rain (the hour's) with the cloud cover, in
tiles/metnordic-1km-wx: the same 1 km and zooms.

    python metnordic_tiles.py [--set weather] [--force] [--local DIR] [--hours N]
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np

import metnordic_map as mn
import quality
import regional_tiles
import temp_map
import tiler

ZOOMS = range(4, 8)
FADE_KM = 50
ROWS, COLS = 2321, 1796
BOX = (49.0, 74.0, -6.0, 42.0)
NAMES = (("speed", "wind_speed_10m"), ("direction", "wind_direction_10m"), ("gust", "wind_speed_of_gust"))
WEATHER = (("speed", "wind_speed_10m"), ("air", "air_temperature_2m"), ("humidity", "relative_humidity_2m"),
           ("pressure", "air_pressure_at_sea_level"), ("rain", "precipitation_amount"), ("cloud", "cloud_area_fraction"))
CREDIT = "MET Nordic, MET Norway (CC BY 4.0)"


def place(lon, lat):
    gx, gy = mn.lcc(lat, lon)
    col, row = (gx - mn.X0) / mn.STEP, (gy - mn.Y0) / mn.STEP
    inside = (col >= 0) & (row >= 0) & (col <= COLS - 1) & (row <= ROWS - 1)
    cells = np.minimum(np.minimum(col, COLS - 1 - col), np.minimum(row, ROWS - 1 - row))
    return col, row, np.where(inside, np.clip(cells * mn.STEP / 1000 / FADE_KM, 0, 1), 0)


def sample(values, col, row):
    c = np.clip(col, 0, COLS - 1.001)
    r = np.clip(row, 0, ROWS - 1.001)
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
    fc, fr = c - c0, r - r0
    top = values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc
    bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc
    return top * (1 - fr) + bottom * fr


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    weather = "--set" in args and args[args.index("--set") + 1] == "weather"
    def ask(name):
        # MET Norway's server answers late or with 500 now and then (15.10.2026: for hours on end):
        # five tries, waiting longer each time.
        for attempt in range(5):
            try:
                return mn.ascii_values(mn.get(f"{mn.SOURCE}.ascii?{name}", timeout=120), name)
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(30 * (attempt + 1))
    times = ask("time")
    run = ask("forecast_reference_time")[0]
    run_time = datetime.fromtimestamp(run, timezone.utc)
    out = tiler.Run("metnordic-1km-wx" if weather else "metnordic-1km", run_time, ZOOMS,
                    "MET Nordic 1 km (air and rain)" if weather else "MET Nordic 1 km", CREDIT, "--force" in args, local)
    if not out.wanted:
        print(f"MET Nordic {run_time:%Y%m%dT%HZ} already published")
        return
    # That run's own file ("latest" moves on within the hour).
    own = mn.SOURCE.replace("_latest.nc", f"_{run_time:%Y%m%dT%HZ}.nc")
    try:
        mn.get(own + ".das")
        mn.SOURCE = own
    except Exception:
        print("its own file not found, reading the latest", flush=True)
    steps = regional_tiles.hours_wanted(times, run, hourly=not weather)
    # The air and rain without the run's own hour (its rain is the hour before the run: 255
    # everywhere, caught by R3's check 13.10.2026).
    if weather:
        steps = [s for s in steps if times[s] > run]
    if limit:
        steps = steps[:limit]
    regional_tiles.STRIDE = 1
    started = time.time()
    placement = tiler.Placement(tiler.tiles_in_box(BOX, ZOOMS), place)
    # The spots' wind (or air, rain, clouds) from MET Nordic's own 1 km cells (spot_cells.py; the
    # owner, 18.10.2026).
    import spot_cells
    try:
        cells = spot_cells.SpotCells(None if weather else "metnordic-1km", "metno_nordic",
                                     weather="metnordic-1km-wx" if weather else None)
    except Exception as error:
        print(f"MET Nordic: the spots' cells left out ({error})", flush=True)
        cells = None
    print(f"MET Nordic {run_time:%Y%m%dT%HZ}: {len(placement.parts)} tiles, {len(steps)} hours", flush=True)
    # Eight hours at a time (each field about 130 MB for 8 hours at every cell).
    for k in range(0, len(steps), 8):
        chunk = steps[k:k + 8]
        names = WEATHER if weather else NAMES
        with ThreadPoolExecutor(len(names)) as pool:
            fields = dict(zip([key for key, _ in names],
                              pool.map(lambda pair: regional_tiles.fetch_hours(pair[1], chunk, ROWS, COLS), names)))
        if weather:
            for s in chunk:
                t = int(times[s])
                raw = fields["air"][s]
                air = raw - 273.15
                humidity = np.clip(np.nan_to_num(fields["humidity"][s], nan=0.6), 0, 1)
                feels = temp_map.feels_like(np.nan_to_num(air), np.nan_to_num(fields["speed"][s]), humidity)
                hpa = fields["pressure"][s] / 100
                rain = np.maximum(np.nan_to_num(fields["rain"][s]), 0)
                cloud = np.clip(np.nan_to_num(fields["cloud"][s]), 0, 1)
                if not out.checked("MET Nordic", t, quality.weather(air, rain)):
                    continue
                if cells is not None:
                    cells.add_placed(t, place, sample, None, None, None, air=air, rain=rain, cloud=cloud)
                valid = lambda at: np.isfinite(at(raw))  # noqa: E731
                out.put(t, tiler.pack(placement, sample, lambda at: tiler.temp_rgb(at(np.nan_to_num(air)), at(feels), at(hpa)), valid), "temp")
                out.put(t, tiler.pack(placement, sample, lambda at: tiler.rain_rgb(at(rain), at(cloud)), valid), "rain")
            print(f"  {len(out.frames)} hours, {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
            continue
        for s in chunk:
            speed, degrees = np.nan_to_num(fields["speed"][s]), np.radians(np.nan_to_num(fields["direction"][s]))
            east, north = -speed * np.sin(degrees), -speed * np.cos(degrees)
            gust = np.nan_to_num(fields["gust"][s])
            raw = fields["speed"][s]
            if not out.checked("MET Nordic", int(times[s]), quality.wind(east, north, gust)):
                continue
            if cells is not None:
                cells.add_placed(int(times[s]), place, sample, east, north, gust)
            out.put(int(times[s]), tiler.pack(placement, sample, lambda at: tiler.wind_rgb(at(east), at(north), at(gust)),
                                              valid=lambda at: np.isfinite(at(raw))))
        print(f"  {len(out.frames)} hours, {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    out.finish()
    if cells is not None:
        try:
            cells.publish(out.folder, local and local + "/points")
        except Exception as error:
            print(f"MET Nordic: the spots' cells not published ({error})", flush=True)


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up: their exit crashed Python after the work was published
    # ("double free or corruption", 18.10.2026), failing every other run and the workflow's next step.
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
