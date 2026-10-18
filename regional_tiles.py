"""The regional models over their whole area as map tiles (the owner, 12.10.2026: the detailed
maps beyond the Baltic's box): MET Nordic (MET Norway, 1 km; here every second cell, 2 km) over the
Nordic countries and the Baltic, and for the wind DWD's ICON-D2 (2.2 km) over Germany, Benelux,
Denmark, the Alps and western Poland, DMI's HARMONIE DINI (2 km) over the British Isles, the North
Sea and the Channel, KNMI's HARMONIE-AROME (2 km) over the Netherlands and Belgium, and Météo-France's AROME (1.3 km)
over France, the Channel and the Alps' west, and MeteoSwiss's ICON-CH1 (1 km) over the Alps and northern
Italy's lakes, and ItaliaMeteo's ICON-2I (2.2 km) over Italy, its islands and the Adriatic, where MET
Nordic isn't (MET Nordic first; the others averaged by their weights where they overlap). The same packs as the world's tiles
(world_tiles.py: "MGT1", a header, PNG tiles), zooms 4-6 on the web map's grid, one pack an hour
(the wind hourly; the weather hourly for the first day, then every 3 hours), uploaded through station-history as
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
import quality
import world_tiles

SETS = {"wind": {"model": "metnordic", "stride": 2}, "weather": {"model": "metnordic-wx", "stride": 3}}
ZOOMS = range(4, 7)
SIZE = 256
STRIDE = 2
FADE_KM = 50
PUBLISHED = "https://files.meteogust.com/tiles/{model}/index.json"
# Each regional model alone too (the app's Wind model menu, the owner, 12.10.2026): its folder
# (tiles/<folder>/), name and credit.
MODEL_SETS = {
    "ICON-D2": ("icon-d2", "ICON-D2 2.2 km", "ICON-D2, DWD (CC BY 4.0)"),
    "HARMONIE DINI": ("dini", "DINI 2 km", "HARMONIE DINI, DMI (CC BY 4.0)"),
    "HARM-NL": ("harm-nl", "HARM-NL 2 km", "HARMONIE-AROME, KNMI (CC BY 4.0)"),
    "AROME": ("arome", "AROME 1.3 km", "AROME, Météo-France (Etalab 2.0)"),
    "ICON-CH1": ("icon-ch1", "ICON-CH1 1 km", "ICON-CH1, MeteoSwiss"),
    "ICON-2I": ("icon-2i", "ICON-2I 2.2 km", "ICON-2I, ItaliaMeteo (CC BY 4.0)"),
}
# The 1-1.3 km models alone also at zoom 7 (about 1 km a pixel at their latitudes; zoom 6 was 1.7-2
# km: the owner, 18.10.2026, the models' full detail on the map and at a tap).
FINE = {"AROME", "ICON-CH1"}


def hours_wanted(times, run, hourly=False):
    """Hourly for the first day, then every third hour; `hourly`: every hour (the wind: the spots'
    hours come from these tiles, the sea breeze's timing with them; the owner, 18.10.2026)."""
    out = []
    for i, t in enumerate(times):
        ahead = (t - run) / 3600
        if ahead < 0:
            continue
        if hourly or ahead <= 24 or round(ahead) % 3 == 0:
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
        data = fetch_chunk(name, start, end, rows, cols)
        for k, s in enumerate(range(start, end + 1)):
            out[s] = data[k]
    return out


def fetch_chunk(name, start, end, rows, cols):
    """Hours start to end of a variable; when MET Norway's server keeps failing on it (15.10.2026:
    a 500, a dropped connection, an empty reply, two hours running), tried more patiently and then
    in two halves, down to single hours."""
    query = f"{name}[{start}:1:{end}][0:{STRIDE}:{rows - 1}][0:{STRIDE}:{cols - 1}]"
    shape = (end - start + 1, (rows - 1) // STRIDE + 1, (cols - 1) // STRIDE + 1)
    last = None
    for attempt in range(4):
        try:
            raw = mn.get(f"{mn.SOURCE}.dods?" + urllib.request.quote(query, safe=":,"))
            return mn.dods_array(raw, shape)
        except Exception as error:
            last = error
            time.sleep(15 * (attempt + 1))
    if end > start:
        middle = (start + end) // 2
        print(f"  {name} hours {start}-{end}: {last!r}; in two halves", flush=True)
        return np.concatenate([fetch_chunk(name, start, middle, rows, cols), fetch_chunk(name, middle + 1, end, rows, cols)])
    raise last


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
            # The tiles between 33-76N and 45W-50E (the Nordics, and the others' areas down to France and Italy;
            # west to DINI's whole domain, Greenland's coast and Iceland: the owner's screenshot, 14.10.2026, a
            # straight line at 22.5W where the tiles stopped), those a model reaches.
            x0, x1 = int((-45 + 180) / 360 * n), int((50 + 180) / 360 * n)
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
        # Our own user agent (the file store refuses Python's own: world_tiles.published).
        request = urllib.request.Request(PUBLISHED.format(model=model) + "?t=%d" % time.time(),
                                         headers={"User-Agent": "MeteoGust helper"})
        with urllib.request.urlopen(request, timeout=30) as reply:
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
        # MET Norway's server answers 500 now and then (15.10.2026: for hours on end): five tries,
        # waiting longer each time.
        for attempt in range(5):
            try:
                return mn.ascii_values(mn.get(f"{mn.SOURCE}.ascii?{query}", timeout=120), name)
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(30 * (attempt + 1))
    times = ask("time", "time")
    run = ask("forecast_reference_time", "forecast_reference_time")[0]
    run_name = datetime.fromtimestamp(run, timezone.utc).strftime("%Y%m%dT%HZ")
    published = None if local else published_run(model)
    if not local and "--force" not in args and published == run_name:
        print(f"MET Nordic {run_name} already published")
        return
    # The same run published again (--force, or two runs at once): a folder of its own, since the
    # packs are kept a year in caches (Cloudflare's, the phones'), and a second copy under the same
    # names went unseen (12.10.2026: AROME's corner storms stayed after their fix).
    if published and published.split("-")[0] == run_name:
        run_name += "-" + format(int(time.time()) % 36 ** 6, "x")
    # That run's own file from here on: "latest" moves to the next run within the hour, and a request
    # made for the old one then fails (12.10.2026: HTTP 400 half an hour in) or mixes two runs.
    own = mn.SOURCE.replace("_latest.nc", f"_{run_name}.nc")
    try:
        mn.get(own + ".das")
        mn.SOURCE = own
    except Exception:
        print(f"MET Nordic {run_name}: its own file not found, reading the latest", flush=True)
    steps = hours_wanted(times, run, hourly=which == "wind")
    # The air and rain without the run's own hour: its rain is the hour before the run, not a
    # forecast (it read as 255 everywhere: R3's check, 13.10.2026).
    if which != "wind":
        steps = [s for s in steps if times[s] > run]
    if limit:
        steps = steps[:limit]
    rows, cols = 2321, 1796
    srows, scols = (rows - 1) // STRIDE + 1, (cols - 1) // STRIDE + 1
    # The other regional models (each may be missing: the rest go on): {name: (place(lon, lat) ->
    # col, row, weight; {unix time: data}; sample(grid, col, row))}, the data the wind's (east,
    # north, gust) or the weather's ({air, rh, wind, rain, cloud}).
    others = {}
    # What went in (for the index, the admin page): each model's run and hours, or why not.
    models = [{"name": "MET Nordic", "run": datetime.fromtimestamp(run, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
               "hours": len(steps)}]
    wanted_times = [int(times[s]) for s in steps]
    wind_set = which == "wind"

    report = quality.Report(model)

    def add(name, make):
        try:
            place, data, sample, run_time = make()
            # Each hour checked; a model with over a fifth of them wrong is left out (DINI's rain
            # read as a rate, AROME's missing corners: 12.10.2026).
            bad = 0
            for t in list(data):
                d = data[t]
                reason = quality.wind(*d) if wind_set else quality.weather(d.get("air"), d.get("rain"))
                report.count()
                if reason:
                    report.fail(name, t, reason)
                    del data[t]
                    bad += 1
            if bad > 0.2 * (len(data) + bad):
                raise RuntimeError(f"check: {bad} hours failed ({report.failures[-1]['reason']})")
            others[name] = (place, data, sample)
            models.append({"name": name, "run": f"{run_time:%Y-%m-%dT%H:%MZ}", "hours": len(data)})
            print(f"{name} {run_time:%Y%m%dT%HZ}: {len(data)} hours", flush=True)
        except Exception as error:
            models.append({"name": name, "error": str(error)[:200]})
            print(f"{name} left out: {error}", flush=True)

    def icon_d2():
        run_time = icond2.newest_run()
        if run_time is None:
            raise RuntimeError("no complete run")
        icon_time = int(run_time.timestamp())
        if wind_set:
            hours = sorted({round((t - icon_time) / 3600) for t in wanted_times} & set(range(icond2.LAST + 1)))
            icon = {name: icond2.fields(run_time, var, hours)
                    for name, var in (("u", "u_10m"), ("v", "v_10m"), ("gust", "vmax_10m"))}
            data = {icon_time + k * 3600: (icon["u"][k], icon["v"][k], icon["gust"][k]) for k in hours}
            weights = icond2.weight_grid(next(iter(icon["u"].values())))
        else:
            data = icond2.weather(run_time, wanted_times)
            weights = icond2.weight_grid(next(iter(data.values()))["air"])

        def place(lon, lat):
            c, r, on = icond2.place(lon, lat)
            return c, r, np.where(on, np.nan_to_num(icond2.sample(weights, c, r)), 0)
        return place, data, icond2.sample, run_time

    def module(model):
        def make():
            instance = model()
            data = instance.winds(wanted_times) if wind_set else instance.weather(wanted_times)
            return instance.place, data, instance.sample, instance.run
        return make

    add("ICON-D2", icon_d2)
    add("HARMONIE DINI", module(dini.Model))
    add("HARM-NL", module(knmi.Model))
    add("AROME", module(arome.Model))
    add("ICON-CH1", module(icon_ch1.Model))
    add("ICON-2I", module(icon2i.Model))
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
            # MET Nordic's own hour checked too (R3): a wrong one is left out.
            report.count()
            reason = quality.wind(east, north, gust)
            if reason:
                report.fail("MET Nordic", t, reason)
                continue
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
        mm = np.maximum(np.nan_to_num(fields["rain"][s]), 0)
        cloud = np.clip(np.nan_to_num(fields["cloud"][s]), 0, 1)
        report.count()
        reason = quality.weather(celsius, mm)
        if reason:
            report.fail("MET Nordic", t, reason)
            continue

        def temp_rgb(air, feel, pressure):
            return np.stack([temp_map.encode(air), temp_map.encode(feel), np.clip(np.rint((pressure - 940) * 2), 1, 255)], axis=-1)

        def rain_rgb(amount, cover):
            return np.stack([np.clip(np.rint(np.sqrt(np.maximum(amount, 0)) * 50), 0, 255),
                             np.clip(np.rint(cover * 255), 0, 255), np.zeros(amount.shape)], axis=-1)

        # The others at this hour: their air (the pressure left as MET Nordic's or ECMWF's: the
        # isobars come from ECMWF's anyway) and rain, averaged by their weights where they overlap,
        # together counting what MET Nordic leaves.
        extra_temp, extra_rain = None, None
        present = [(name, others[name][1][t]) for name in others if t in others[name][1]]
        if present:
            extra_temp, extra_rain = {}, {}
            for key in placer.tiles:
                sums_t, sums_r, w_sum, miss = 0, 0, 0, 1
                for name, data in present:
                    c, r, w = parts[name][key]
                    sample = others[name][2]
                    air = sample(data["air"], c, r)
                    w = np.where(np.isfinite(air), w, 0)
                    if not np.any(w > 0):
                        continue
                    rh, speed = sample(data["rh"], c, r), sample(data["wind"], c, r)
                    feel = temp_map.feels_like(np.nan_to_num(air), np.nan_to_num(speed), np.nan_to_num(rh, nan=0.6))
                    rgb_t = temp_rgb(np.nan_to_num(air), feel, np.full(air.shape, 1013.0)).astype(np.float32)
                    rgb_r = rain_rgb(np.nan_to_num(sample(data["rain"], c, r)), np.nan_to_num(sample(data["cloud"], c, r))).astype(np.float32)
                    sums_t = sums_t + rgb_t * w[..., None]
                    sums_r = sums_r + rgb_r * w[..., None]
                    w_sum = w_sum + w
                    miss = miss * (1 - w)
                if np.max(w_sum) <= 0:
                    continue
                share = (1 - miss) * (1 - weights[key])
                extra_temp[key] = (sums_t / np.maximum(w_sum, 1e-6)[..., None], share)
                extra_rain[key] = (sums_r / np.maximum(w_sum, 1e-6)[..., None], share)
        temp = packed(lambda at: temp_rgb(at(celsius), at(feels), at(hpa)), extra_temp)
        rain = packed(lambda at: rain_rgb(at(mm), at(cloud)), extra_rain)
        put(f"{run_name}/{t}-temp.pack", temp)
        put(f"{run_name}/{t}-rain.pack", rain)
        frames.append({"time": t, "file": f"{run_name}/{t}-temp.pack", "temp": f"{run_name}/{t}-temp.pack",
                       "rain": f"{run_name}/{t}-rain.pack"})
        total += len(temp) + len(rain)
    index = {"model": "MET Nordic 1 km" if which == "wind" else "MET Nordic 1 km (air and rain)", "credit": "MET Norway, MET Nordic (CC BY 4.0)",
             "run": datetime.fromtimestamp(run, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "runName": run_name,
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "zooms": [ZOOMS.start, ZOOMS.stop - 1],
             "land": None, "models": models, "frames": frames}
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (local / "index.json").write_bytes(body)
    else:
        print(world_tiles.upload(f"{model}/index.json", body).strip())
        try:
            world_tiles.upload(f"{model}/check.json", report.body(index["run"], True))
        except Exception as error:
            print(f"{model}: check.json not sent ({error})", flush=True)
    print(f"MET Nordic {run_name}: {len(frames)} hours, {total / 1e6:.0f} MB, {time.time() - started:.0f} s")
    if which != "wind":
        # Each model's own air and rain too (tiles/<folder>-wx/: the weather mix's; the owner,
        # 18.10.2026). One failing leaves the others.
        for name, (place, data, sample) in others.items():
            if name not in MODEL_SETS or not data:
                continue
            try:
                model_weather_set(name, data, sample, parts[name], [m for m in models if m["name"] == name][0], run_name, local)
            except Exception as error:
                print(f"{name} weather alone left out: {error}", flush=True)
        return
    # Each model alone: its tiles where it counts (its weight as the alpha: the app draws the
    # world's model under it), at this run's hours it has. One failing leaves the others.
    for name, (place, data, sample) in others.items():
        if name not in MODEL_SETS or not data:
            continue
        try:
            model_set(name, data, sample, parts[name], [m for m in models if m["name"] == name][0], run_name, local,
                      place=others[name][0])
        except Exception as error:
            print(f"{name} alone left out: {error}", flush=True)


def model_weather_set(name, data, sample, parts, info, run_name, local):
    """One regional model's own air and rain (tiles/<folder>-wx/<run_name>/<time>-temp.pack and
    -rain.pack, as the world's: air, feels like and no pressure; the hour's rain and the cloud
    cover), its weight as the alpha, and its index."""
    folder, label, credit = MODEL_SETS[name]
    folder += "-wx"
    started = time.time()
    keys = [key for key, (c, r, w) in parts.items() if np.any(w > 0)]
    frames, total = [], 0

    def put(file, pack):
        if local:
            (local / folder / run_name).mkdir(parents=True, exist_ok=True)
            (local / folder / file).write_bytes(pack)
        else:
            world_tiles.upload(f"{folder}/{file}", pack)

    for t in sorted(data):
        d = data[t]
        temp_tiles, rain_tiles = {}, {}
        for key in keys:
            c, r, w = parts[key]
            air = sample(d["air"], c, r)
            w = np.where(np.isfinite(air), w, 0)
            if not np.any(w > 0):
                continue
            alpha = np.rint(np.clip(w, 0, 1) * 255)[..., None]
            rh, speed = sample(d["rh"], c, r), sample(d["wind"], c, r)
            feel = temp_map.feels_like(np.nan_to_num(air), np.nan_to_num(speed), np.clip(np.nan_to_num(rh, nan=0.6), 0, 1))
            rgb_t = np.stack([temp_map.encode(air), temp_map.encode(feel), np.zeros(air.shape)], axis=-1)
            mm = np.maximum(np.nan_to_num(sample(d["rain"], c, r)), 0)
            cover = np.clip(np.nan_to_num(sample(d["cloud"], c, r)), 0, 1)
            rgb_r = np.stack([np.clip(np.rint(np.sqrt(mm) * 50), 0, 255), np.clip(np.rint(cover * 255), 0, 255), np.zeros(mm.shape)], axis=-1)
            temp_tiles[key] = world_tiles.png(np.concatenate([rgb_t, alpha], axis=-1).astype(np.uint8), "RGBA")
            rain_tiles[key] = world_tiles.png(np.concatenate([rgb_r, alpha], axis=-1).astype(np.uint8), "RGBA")
        if not temp_tiles:
            continue
        temp = world_tiles.encode(temp_tiles, {z: SIZE for z in ZOOMS})
        rain = world_tiles.encode(rain_tiles, {z: SIZE for z in ZOOMS})
        put(f"{run_name}/{t}-temp.pack", temp)
        put(f"{run_name}/{t}-rain.pack", rain)
        frames.append({"time": int(t), "file": f"{run_name}/{t}-temp.pack", "temp": f"{run_name}/{t}-temp.pack",
                       "rain": f"{run_name}/{t}-rain.pack"})
        total += len(temp) + len(rain)
    index = {"model": label + " (air and rain)", "credit": credit, "run": info.get("run"), "runName": run_name,
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "zooms": [ZOOMS.start, ZOOMS.stop - 1],
             "land": None, "frames": frames}
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (local / folder).mkdir(parents=True, exist_ok=True)
        (local / folder / "index.json").write_bytes(body)
    else:
        world_tiles.upload(f"{folder}/index.json", body)
    print(f"{label} air and rain: {len(keys)} tiles, {len(frames)} hours, {total / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)


def model_set(name, data, sample, parts, info, run_name, local, place=None):
    """One regional model's own tiles (tiles/<folder>/<run_name>/<time>.pack) and index; the FINE
    ones at zoom 7 too (the four under each zoom-6 tile it has)."""
    folder, label, credit = MODEL_SETS[name]
    started = time.time()
    keys = [key for key, (c, r, w) in parts.items() if np.any(w > 0)]
    zooms = list(ZOOMS)
    if name in FINE and place is not None:
        parts = dict(parts)
        for z, x, y in [k for k in keys if k[0] == ZOOMS.stop - 1]:
            for dx in (0, 1):
                for dy in (0, 1):
                    child = (z + 1, 2 * x + dx, 2 * y + dy)
                    lon, lat = tile_lonlat(*child)
                    found = place(lon, lat)
                    if np.any(found[2] > 0):
                        parts[child] = found
                        keys.append(child)
        zooms.append(ZOOMS.stop)

    def rgb(e, n, g):
        return np.stack([np.clip(np.rint(e * 4 + 128), 0, 255), np.clip(np.rint(n * 4 + 128), 0, 255),
                         np.clip(np.rint(g * 5), 0, 255)], axis=-1)

    # The spots' wind from the model's own cells (spot_cells.py; the owner, 18.10.2026).
    import spot_cells
    keys = {"ICON-D2": "icon_d2", "HARMONIE DINI": "dmi_harmonie_arome_europe", "HARM-NL": "knmi_harmonie_arome_netherlands",
            "AROME": "meteofrance_arome_france_hd", "ICON-CH1": "meteoswiss_icon_ch1", "ICON-2I": "italia_meteo_arpae_icon_2i"}
    try:
        cells = spot_cells.SpotCells(folder, keys[name]) if place is not None and name in keys else None
    except Exception as error:
        print(f"{label}: the spots' cells left out ({error})", flush=True)
        cells = None
    frames, total = [], 0
    for t in sorted(data):
        e, n, g = data[t]
        if cells is not None:
            cells.add_placed(t, place, sample, e, n, g)
        tiles = {}
        for key in keys:
            c, r, w = parts[key]
            u, v, gg = sample(e, c, r), sample(n, c, r), sample(g, c, r)
            w = np.where(np.isfinite(u) & np.isfinite(v), w, 0)
            alpha = np.rint(np.clip(w, 0, 1) * 255)[..., None]
            colour = rgb(np.nan_to_num(u), np.nan_to_num(v), np.nan_to_num(gg))
            tiles[key] = world_tiles.png(np.concatenate([colour, alpha], axis=-1).astype(np.uint8), "RGBA")
        pack = world_tiles.encode(tiles, {z: SIZE for z in zooms})
        file = f"{run_name}/{t}.pack"
        if local:
            (local / folder / run_name).mkdir(parents=True, exist_ok=True)
            (local / folder / file).write_bytes(pack)
        else:
            world_tiles.upload(f"{folder}/{file}", pack)
        frames.append({"time": int(t), "file": file})
        total += len(pack)
    index = {"model": label, "credit": credit, "run": info.get("run"), "runName": run_name,
             "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "zooms": [zooms[0], zooms[-1]],
             "land": None, "frames": frames}
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (local / folder / "index.json").write_bytes(body)
    else:
        world_tiles.upload(f"{folder}/index.json", body)
    print(f"{label} alone: {len(keys)} tiles, {len(frames)} hours, {total / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    if cells is not None:
        try:
            cells.publish(run_name, str(local / "points") if local else None)
        except Exception as error:
            print(f"{label}: the spots' cells not published ({error})", flush=True)


if __name__ == "__main__":
    main()
