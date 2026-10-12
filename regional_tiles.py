"""The regional models over their whole area as map tiles (the owner, 12.10.2026: the detailed
maps beyond the Baltic's box): MET Nordic (MET Norway, 1 km; here every second cell, 2 km) over the
Nordic countries and the Baltic, and for the wind DWD's ICON-D2 (2.2 km) over Germany, Benelux,
Denmark, the Alps and western Poland, DMI's HARMONIE DINI (2 km) over the British Isles, the North
Sea and the Channel, KNMI's HARMONIE-AROME (2 km) over the Netherlands and Belgium, and Météo-France's AROME (1.3 km)
over France, the Channel and the Alps' west, and MeteoSwiss's ICON-CH1 (1 km) over the Alps and northern
Italy's lakes, and ItaliaMeteo's ICON-2I (2.2 km) over Italy, its islands and the Adriatic, where MET
Nordic isn't (MET Nordic first; the others averaged by their weights where they overlap). The same packs as the world's tiles
(world_tiles.py: "MGT1", a header, PNG tiles), zooms 4-6 on the web map's grid, one pack an hour
(hourly for the first day, then every 3 hours), uploaded through station-history as
tiles/metnordic/; the app draws them between our Baltic mix and ECMWF's world.

Each tile RGBA: red the east wind x 4 + 128, green the north wind x 4 + 128, blue the gust x 5
(as the world tiles), alpha how much the model counts there: 255 inside, falling to 0 over the
last 50 km to its area's edge (the app blends it into the world's there).

Also (--set weather, every 6 hours, every third cell: 3 km) the air and the rain as the world's
temperature and rain packs (world_tiles.temp_pack / rain_pack: <time>-temp.pack, <time>-rain.pack)
with the same alpha, uploaded as tiles/metnordic-wx/ (its index's frames carry "temp" and "rain").

    python regional_tiles.py              make and upload the newest run when it's new
    python regional_tiles.py --force      even when published
    python regional_tiles.py --local DIR  write the packs to DIR (a test; --hours N for fewer)
"""
import json
import math
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import arome
import dini
import icon2i
import icon_ch1
import icond2
import knmi
import metnordic_map as mn
import temp_map
import world_tiles

SETS = {"wind": {"model": "metnordic", "stride": 2}, "weather": {"model": "metnordic-wx", "stride": 3}}
ZOOMS = range(4, 7)
SIZE = 256
STRIDE = 2
FADE_KM = 50
PUBLISHED = "https://files.meteogust.com/tiles/{model}/index.json"


def hours_wanted(times, run):
    """Hourly for the first day, then every third hour."""
    out = []
    for i, t in enumerate(times):
        ahead = (t - run) / 3600
        if ahead < 0:
            continue
        if ahead <= 24 or round(ahead) % 3 == 0:
            out.append(i)
    return out


def fetch_hours(name, steps, rows, cols):
    """A variable at the given steps over the whole grid, every STRIDE cells (chunks of 8 hours)."""
    out = {}
    runs, current = [], []
    for s in steps:
        if current and (s != current[-1] + 1 or len(current) == 8):
            runs.append(current)
            current = []
        current.append(s)
    if current:
        runs.append(current)
    for chunk in runs:
        start, end = chunk[0], chunk[-1]
        query = f"{name}[{start}:1:{end}][0:{STRIDE}:{rows - 1}][0:{STRIDE}:{cols - 1}]"
        shape = (end - start + 1, (rows - 1) // STRIDE + 1, (cols - 1) // STRIDE + 1)
        for attempt in range(3):
            try:
                raw = mn.get(f"{mn.SOURCE}.dods?" + urllib.request.quote(query, safe=":,"))
                data = mn.dods_array(raw, shape)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(10)
        for k, s in enumerate(range(start, end + 1)):
            out[s] = data[k]
    return out


def tile_lonlat(z, x, y):
    """Each pixel centre's longitude and latitude of tile z/x/y (rows from the north)."""
    n = 1 << z
    px = (np.arange(SIZE) + 0.5) / SIZE
    lon = (x + px) / n * 360 - 180
    merc = math.pi * (1 - 2 * (y + px) / n)
    lat = np.degrees(np.arctan(np.sinh(merc)))
    return np.meshgrid(lon, lat)


class Placer:
    """Where each tile's pixels fall on the strided grid (fractional column and row), worked out
    once per run for every tile the grid reaches."""

    def __init__(self, rows, cols, extra=None):
        """`extra`: another model's test of a tile's points (lon, lat), whose tiles count too."""
        self.rows, self.cols = rows, cols
        self.tiles = {}
        for z in ZOOMS:
            n = 1 << z
            # The tiles between 33-76N and 15W-50E (the Nordics, and the others' areas down to France and Italy),
            # those a model reaches.
            x0, x1 = int((-15 + 180) / 360 * n), int((50 + 180) / 360 * n)
            def ty(lat):
                return int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
            for y in range(ty(76), ty(33) + 1):
                for x in range(x0, x1 + 1):
                    lon, lat = tile_lonlat(z, x, y)
                    gx, gy = mn.lcc(lat, lon)
                    col = (gx - mn.X0) / (mn.STEP * STRIDE)
                    row = (gy - mn.Y0) / (mn.STEP * STRIDE)
                    inside = (col >= 0) & (row >= 0) & (col <= cols - 1) & (row <= rows - 1)
                    if inside.any() or (extra is not None and extra(lon, lat)):
                        self.tiles[(z, x, y)] = (col, row, inside, lon, lat)

    def weight(self, col, row, inside, lon=None, lat=None):
        """1 inside, falling to 0 over the last FADE_KM to the grid's edge."""
        edge = np.minimum(np.minimum(col, self.cols - 1 - col), np.minimum(row, self.rows - 1 - row)) * STRIDE
        return np.where(inside, np.clip(edge / FADE_KM, 0, 1), 0)

    def sample(self, grid, col, row):
        """Bilinear between the grid's cells (clamped at its edge)."""
        c = np.clip(col, 0, self.cols - 1.001)
        r = np.clip(row, 0, self.rows - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        g = np.nan_to_num(grid)
        top = g[r0, c0] * (1 - fc) + g[r0, c0 + 1] * fc
        bottom = g[r0 + 1, c0] * (1 - fc) + g[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr


def published_run(model):
    try:
        with urllib.request.urlopen(PUBLISHED.format(model=model) + "?t=%d" % time.time(), timeout=30) as reply:
            return json.load(reply).get("runName")
    except Exception:
        return None


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    which = args[args.index("--set") + 1] if "--set" in args else "wind"
    global STRIDE
    STRIDE = SETS[which]["stride"]
    model = SETS[which]["model"]
    started = time.time()
    def ask(query, name):
        # MET Norway's server answers 500 now and then: three tries.
        for attempt in range(3):
            try:
                return mn.ascii_values(mn.get(f"{mn.SOURCE}.ascii?{query}"), name)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(15)
    times = ask("time", "time")
    run = ask("forecast_reference_time", "forecast_reference_time")[0]
    run_name = datetime.fromtimestamp(run, timezone.utc).strftime("%Y%m%dT%HZ")
    if not local and "--force" not in args and published_run(model) == run_name:
        print(f"MET Nordic {run_name} already published")
        return
    steps = hours_wanted(times, run)
    if limit:
        steps = steps[:limit]
    rows, cols = 2321, 1796
    srows, scols = (rows - 1) // STRIDE + 1, (cols - 1) // STRIDE + 1
    # The other regional models with the wind (each may be missing: the rest go on): {name:
    # (place(lon, lat) -> col, row, weight; {unix time: (east, north, gust)}; sample(grid, col, row))}.
    others = {}
    wanted_times = [int(times[s]) for s in steps]
    if which == "wind":
        try:
            icon_run = icond2.newest_run()
            if icon_run is not None:
                icon_time = int(icon_run.timestamp())
                hours = sorted({round((t - icon_time) / 3600) for t in wanted_times} & set(range(icond2.LAST + 1)))
                icon = {name: icond2.fields(icon_run, var, hours)
                        for name, var in (("u", "u_10m"), ("v", "v_10m"), ("gust", "vmax_10m"))}
                icon_weights = icond2.weight_grid(next(iter(icon["u"].values())))

                def icon_place(lon, lat):
                    c, r, on = icond2.place(lon, lat)
                    return c, r, np.where(on, np.nan_to_num(icond2.sample(icon_weights, c, r)), 0)
                winds = {icon_time + k * 3600: (icon["u"][k], icon["v"][k], icon["gust"][k]) for k in hours}
                others["ICON-D2"] = (icon_place, winds, icond2.sample)
                print(f"ICON-D2 {icon_run:%Y%m%dT%HZ}: {len(hours)} hours", flush=True)
        except Exception as error:
            print(f"ICON-D2 left out: {error}", flush=True)
        try:
            harmonie = dini.Model()
            winds = harmonie.winds(wanted_times)
            others["DINI"] = (harmonie.place, winds, harmonie.sample)
            print(f"HARMONIE DINI {harmonie.run:%Y%m%dT%HZ}: {len(winds)} hours, rows {harmonie.height} x {harmonie.width}", flush=True)
        except Exception as error:
            print(f"HARMONIE DINI left out: {error}", flush=True)
        try:
            dutch = knmi.Model()
            winds = dutch.winds(wanted_times)
            others["HARM-NL"] = (dutch.place, winds, dutch.sample)
            print(f"HARM-NL {dutch.run:%Y%m%dT%HZ}: {len(winds)} hours", flush=True)
        except Exception as error:
            print(f"HARM-NL left out: {error}", flush=True)
        try:
            french = arome.Model()
            winds = french.winds(wanted_times)
            others["AROME"] = (french.place, winds, french.sample)
            print(f"AROME {french.run:%Y%m%dT%HZ}: {len(winds)} hours", flush=True)
        except Exception as error:
            print(f"AROME left out: {error}", flush=True)
        try:
            swiss = icon_ch1.Model()
            winds = swiss.winds(wanted_times)
            others["ICON-CH1"] = (swiss.place, winds, swiss.sample)
            print(f"ICON-CH1 {swiss.run:%Y%m%dT%HZ}: {len(winds)} hours", flush=True)
        except Exception as error:
            print(f"ICON-CH1 left out: {error}", flush=True)
        try:
            italian = icon2i.Model()
            winds = italian.winds(wanted_times)
            others["ICON-2I"] = (italian.place, winds, italian.sample)
            print(f"ICON-2I {italian.run:%Y%m%dT%HZ}: {len(winds)} hours", flush=True)
        except Exception as error:
            print(f"ICON-2I left out: {error}", flush=True)
    placer = Placer(srows, scols, extra=(lambda lon, lat: any(place(lon, lat)[2].any() for place, _, _ in others.values()))
                    if others else None)
    print(f"{len(placer.tiles)} tiles, {len(steps)} hours, run {run_name}", flush=True)
    names = (("speed", "wind_speed_10m"), ("direction", "wind_direction_10m"), ("gust", "wind_speed_of_gust")) if which == "wind"         else (("speed", "wind_speed_10m"), ("air", "air_temperature_2m"), ("humidity", "relative_humidity_2m"),
              ("pressure", "air_pressure_at_sea_level"), ("rain", "precipitation_amount"), ("cloud", "cloud_area_fraction"))
    fields = {key: fetch_hours(name, steps, rows, cols) for key, name in names}
    print(f"read in {time.time() - started:.0f} s", flush=True)
    weights = {key: placer.weight(*value) for key, value in placer.tiles.items()}
    # The other models on each tile: where they lie and how much they count there.
    parts = {name: {key: model[0](lon, lat) for key, (col, row, inside, lon, lat) in placer.tiles.items()}
             for name, model in others.items()}
    frames, total = [], 0
    def put(name, data):
        if local:
            (local / run_name).mkdir(parents=True, exist_ok=True)
            (local / name).write_bytes(data)
        else:
            world_tiles.upload(f"{model}/{name}", data)

    def packed(make, extra=None):
        """Each tile's RGB from `make` (given how to sample a grid there) and its alpha; `extra`:
        the ICON-D2 part (key -> its RGB and weight), blended in where MET Nordic is less."""
        tiles = {}
        for key, (col, row, inside, lon, lat) in placer.tiles.items():
            rgb = make(lambda grid: placer.sample(grid, col, row)).astype(np.float32)
            weight = weights[key]
            if extra is not None and key in extra:
                other, w2 = extra[key]
                total = weight + w2
                rgb = np.where(total[..., None] > 0, (rgb * weight[..., None] + other * w2[..., None]) / np.maximum(total, 1e-6)[..., None], rgb)
                weight = total
            alpha = np.rint(np.clip(weight, 0, 1) * 255)[..., None]
            tiles[key] = world_tiles.png(np.concatenate([rgb, alpha], axis=-1).astype(np.uint8), "RGBA")
        return world_tiles.encode(tiles, {z: SIZE for z in ZOOMS})

    for s in steps:
        t = int(times[s])
        if which == "wind":
            speed, direction, gust = fields["speed"][s], fields["direction"][s], fields["gust"][s]
            rad = np.radians(np.nan_to_num(direction))
            east, north = -np.nan_to_num(speed) * np.sin(rad), -np.nan_to_num(speed) * np.cos(rad)
            def encode(e, n, g):
                return np.stack([np.clip(np.rint(e * 4 + 128), 0, 255), np.clip(np.rint(n * 4 + 128), 0, 255),
                                 np.clip(np.rint(g * 5), 0, 255)], axis=-1)
            # The others at this hour: averaged by their weights where they overlap, together
            # counting what MET Nordic leaves (1 - its weight).
            extra = None
            present = [(name, others[name][1][t]) for name in others if t in others[name][1]]
            if present:
                extra = {}
                for key in placer.tiles:
                    rgb_sum, w_sum, miss = 0, 0, 1
                    for name, (e, n, g) in present:
                        c, r, w = parts[name][key]
                        sample = others[name][2]
                        u, v, gg = sample(e, c, r), sample(n, c, r), sample(g, c, r)
                        w = np.where(np.isfinite(u) & np.isfinite(v), w, 0)
                        rgb_sum = rgb_sum + encode(np.nan_to_num(u), np.nan_to_num(v), np.nan_to_num(gg)).astype(np.float32) * w[..., None]
                        w_sum = w_sum + w
                        miss = miss * (1 - w)
                    if np.max(w_sum) <= 0:
                        continue
                    rgb = rgb_sum / np.maximum(w_sum, 1e-6)[..., None]
                    extra[key] = (rgb, (1 - miss) * (1 - weights[key]))
            data = packed(lambda at: encode(at(east), at(north), at(gust)), extra)
            name = f"{run_name}/{t}.pack"
            put(name, data)
            frames.append({"time": t, "file": name})
            total += len(data)
            continue
        # The air (as the world's temperature packs: air, feels like, sea-level pressure) and the
        # rain (sqrt of the hour's mm x 50, the cloud cover x 255).
        celsius = fields["air"][s] - 273.15
        humidity = np.clip(np.nan_to_num(fields["humidity"][s], nan=0.6), 0, 1)
        feels = temp_map.feels_like(celsius, np.nan_to_num(fields["speed"][s]), humidity)
        hpa = fields["pressure"][s] / 100
        temp = packed(lambda at: np.stack([temp_map.encode(at(celsius)), temp_map.encode(at(feels)),
                                           np.clip(np.rint((at(hpa) - 940) * 2), 1, 255)], axis=-1))
        mm = np.maximum(np.nan_to_num(fields["rain"][s]), 0)
        cloud = np.clip(np.nan_to_num(fields["cloud"][s]), 0, 1)
        rain = packed(lambda at: np.stack([np.clip(np.rint(np.sqrt(np.maximum(at(mm), 0)) * 50), 0, 255),
                                           np.clip(np.rint(at(cloud) * 255), 0, 255), np.zeros((SIZE, SIZE))], axis=-1))
        put(f"{run_name}/{t}-temp.pack", temp)
        put(f"{run_name}/{t}-rain.pack", rain)
        frames.append({"time": t, "file": f"{run_name}/{t}-temp.pack", "temp": f"{run_name}/{t}-temp.pack",
                       "rain": f"{run_name}/{t}-rain.pack"})
        total += len(temp) + len(rain)
    index = {"model": "MET Nordic 1 km" if which == "wind" else "MET Nordic 1 km (air and rain)", "credit": "MET Norway, MET Nordic (CC BY 4.0)",
             "run": datetime.fromtimestamp(run, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "runName": run_name,
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "zooms": [ZOOMS.start, ZOOMS.stop - 1],
             "land": None, "frames": frames}
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (local / "index.json").write_bytes(body)
    else:
        print(world_tiles.upload(f"{model}/index.json", body).strip())
    print(f"MET Nordic {run_name}: {len(frames)} hours, {total / 1e6:.0f} MB, {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
