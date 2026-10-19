"""The spots' values from a model's own grid (the owner, 18.10.2026: our wind at the spots read lower
and our air warmer than Open-Meteo's for the same models, by 0.2-0.4 m/s and 0.3-1 °C: our map
pixels blend a cell with its neighbours, at the coast land into the sea's wind and sea into the land's
air; Open-Meteo reads the model's own cell): while a model's run is made into tiles, each spot's wind
from the cell nearest its point for that model (its csPoints: Open-Meteo's cell_selection=nearest)
and its air, rain and clouds from the nearest cell whose centre is on land (cell_selection=land; our
coastline tiles say where land is; the model's own ground where we have it, as Open-Meteo), the air
moved from the cell's ground to the spot's (our terrain; 6.5 °C a km, as Open-Meteo), written as spot_points.py's files (points/<set>.json: [east,
north, gust] m/s; points/<set>-wx.json: [air °C, rain mm an hour, cloud %, low, mid, high]).

    cells = SpotCells("ecmwf", "ecmwf_ifs", weather="ecmwf-wx")
    for each hour: cells.add_regular(t, grid, fields, rate)      # a regular latitude/longitude grid
                or cells.add_placed(t, place, sample, east, north, gust)   # a model's own projection
    cells.publish(run_name, local)
"""
import io
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import spot_points
import world_tiles

COAST = "https://files.meteogust.com/tiles/coast/"
LAND_ZOOM = 9
# How far round the nearest cell a land cell is looked for (cells each way).
LAND_RINGS = 3
LAPSE = 0.0065
TERRAIN = "https://files.meteogust.com/tiles/terrain/"
# The app's model keys -> Open-Meteo's names for their ground (model_heights.py).
GROUND_NAMES = {"ecmwf_ifs": "ecmwf_ifs", "gfs_global": "ncep_gfs013", "icon_global": "dwd_icon", "gem_global": "cmc_gem_gdps",
                "icon_eu": "dwd_icon_eu", "metno_nordic": "metno_nordic_pp", "dmi_harmonie_arome_europe": "dmi_harmonie_arome_europe",
                "meteofrance_arome_france_hd": "meteofrance_arome_france_hd", "icon_d2": "dwd_icon_d2",
                "knmi_harmonie_arome_netherlands": "knmi_harmonie_arome_netherlands", "meteoswiss_icon_ch1": "meteoswiss_icon_ch1",
                "italia_meteo_arpae_icon_2i": "italia_meteo_arpae_icon_2i", "ukmo_uk_deterministic_2km": "ukmo_uk_deterministic_2km",
                "meteoswiss_icon_ch2": "meteoswiss_icon_ch2", "meteofrance_arome_france": "meteofrance_arome_france0025",
                "knmi_harmonie_arome_europe": "knmi_harmonie_arome_europe"}

_terrain = {}


def place_height(lat, lon, z=9):
    """Our terrain's mean height (m) at a place (tiles/terrain, zoom 9: about 150 m), 0 at sea; None
    without the terrain."""
    try:
        if "index" not in _terrain:
            _terrain["index"] = set(json.loads(spot_points.get(TERRAIN + "index.json"))["packs"])
        n = 2 ** z
        x = (lon + 180) / 360 * n * 256
        y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n * 256
        tx, ty = int(x // 256), int(y // 256)
        f = 2 ** (z - 5)
        name = f"{tx // f}-{ty // f}"
        if name not in _terrain["index"]:
            return 0.0
        if name not in _terrain:
            data = spot_points.get(TERRAIN + f"5-{name}.pack")
            k = int.from_bytes(data[4:8], "little")
            _terrain[name] = (json.loads(data[8:8 + k])["tiles"], 8 + k, data)
        tiles, start, data = _terrain[name]
        r = tiles.get(f"{z}/{tx}/{ty}")
        if r is None:
            return 0.0
        tile = np.asarray(Image.open(io.BytesIO(data[start + r[0]:start + r[0] + r[1]])).convert("RGBA"))
        p = tile[min(int(y) - ty * 256, 255), min(int(x) - tx * 256, 255)]
        return float(int(p[0]) * 256 + int(p[1]) - 1000)
    except Exception as error:
        if "failed" not in _terrain:
            print(f"No terrain for the spots' air ({error})", flush=True)
            _terrain["failed"] = True
        return None


class Coast:
    """Land or sea at a place from our coastline tiles (zoom 9, about 300 m: 127.5 on the shore,
    above it land)."""

    def __init__(self):
        index = json.loads(spot_points.get(COAST + "index.json"))
        self.packs, self.land = set(index["packs"]), set(index.get("land", []))
        self.folder = (index["folder"] + "/") if index.get("folder") else ""
        self.readers, self.tiles, self.known = {}, {}, {}

    def is_land(self, lat, lon):
        key = (round(lat, 4), round(lon, 4))
        if key in self.known:
            return self.known[key]
        n = 2 ** LAND_ZOOM
        x = (lon + 180) / 360 * n
        y = (1 - math.asinh(math.tan(math.radians(max(min(lat, 85), -85)))) / math.pi) / 2 * n
        tx, ty = int(x) % n, min(int(y), n - 1)
        name = f"{tx >> (LAND_ZOOM - 5)}-{ty >> (LAND_ZOOM - 5)}"
        if name not in self.packs:
            land = name in self.land
        else:
            if (tx, ty) not in self.tiles:
                if name not in self.readers:
                    self.readers[name] = spot_points.PackReader(COAST + f"{self.folder}5-{name}.pack")
                self.tiles[(tx, ty)] = self.readers[name].tile(LAND_ZOOM, tx, ty)
            tile = self.tiles[(tx, ty)]
            size = tile.shape[0] if tile is not None else 256
            land = tile is not None and tile[min(int((y - int(y)) * size), size - 1), min(int((x - int(x)) * size), size - 1), 0] > 127.5
        self.known[key] = bool(land)
        return self.known[key]


def run_time(name):
    """A points file's run as a time: "20261018T06Z-9km" -> 2026-10-18 06 UTC; None when it doesn't say."""
    try:
        return datetime.strptime(str(name)[:12], "%Y%m%dT%HZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class SpotCells:
    def __init__(self, set_name, model_key, weather=None):
        self.set_name, self.model_key, self.weather_name = set_name, model_key, weather
        spots = [s for s in spot_points.spots() if model_key in s["weights"]]
        # Wind at each spot's point for this model and at the spot itself ("Sea wind" off); the
        # weather at the spot.
        self.wind_points, self.air_points = {}, {}
        for s in spots:
            lat, lon = float(s["lat"]), float(s["lon"])
            self.wind_points[spot_points.key(lat, lon)] = (lat, lon)
            point = (s.get("csPoints") or {}).get(model_key)
            if isinstance(point, list) and len(point) == 2:
                self.wind_points[spot_points.key(*point)] = (float(point[0]), float(point[1]))
            self.air_points[spot_points.key(lat, lon)] = (lat, lon)
        self.coast = None
        self.cells = None
        self.wind, self.air = {}, {}
        self.failed = False
        # Each spot's air shift (°C): from its cell's ground to the spot's own height.
        self.shift = {}
        self._ground = None

    def ground(self):
        """The model's own ground (Open-Meteo's HSURF as it is: the sea -999) and its grid (None for
        ECMWF's own points), once; None without."""
        if self._ground is None:
            try:
                import model_heights
                name = GROUND_NAMES.get(self.model_key, self.model_key)
                values = model_heights.read_hsurf(name, raw=True)
                spec = model_heights.MODELS[name]
                self._ground = (values, None if spec["kind"] == "o1280" else model_heights.Grid(name, spec, values.shape))
            except Exception as error:
                print(f"{self.set_name}: no ground ({error})", flush=True)
                self._ground = False
        return self._ground or None

    def ground_at(self, lat, lon):
        """The model's ground at its grid point nearest a place: (height m, on land), None unknown."""
        g = self.ground()
        if not g or g[1] is None:
            return None
        values, grid = g
        col, row, on = grid.place(np.array([lon]), np.array([lat]))
        if not bool(np.ravel(on)[0]):
            return None
        j = min(max(int(round(float(np.ravel(row)[0]))), 0), values.shape[0] - 1)
        i = int(round(float(np.ravel(col)[0]))) % values.shape[1]
        v = float(values[j, i])
        if not math.isfinite(v):
            return None
        return (max(v, 0.0) if v > -500 else 0.0), v > -500

    def set_shift(self, key, lat, lon, cell_height):
        """The air's shift at a spot from its cell's ground (m) to the spot's own height."""
        here = place_height(lat, lon)
        self.shift[key] = LAPSE * (cell_height - here) if cell_height is not None and here is not None else 0.0

    # MARK: Regular grids (ECMWF, GFS, ICON, GDPS, ICON-EU)

    def _regular_cells(self, grid, shape):
        rows, cols = shape
        lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
        dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
        dlon = grid["iDirectionIncrementInDegrees"]
        wraps = abs(cols * dlon - 360) < 1.5 * dlon
        # A regional grid's first longitude east of 180 (GRIB's 0-360: ICON-EU's 336.5 for 23.5 W) as
        # -180..180, else every spot fell off its west edge (the owner, 18.10.2026: ICON-EU's spot
        # file had only the run before's hours).
        if not wraps and lon0 > 180:
            lon0 -= 360

        def nearest(lat, lon):
            j = int(round((lat - lat0) / dlat))
            i = int(round(((lon - lon0) % 360 if wraps else lon - lon0) / dlon))
            if wraps:
                i %= cols
            return (j, i) if 0 <= j < rows and 0 <= i < cols else None

        def centre(j, i):
            return lat0 + j * dlat, ((lon0 + i * dlon + 180) % 360) - 180

        wind = {k: nearest(*p) for k, p in self.wind_points.items()}
        air = {}
        if self.weather_name:
            self.coast = self.coast or Coast()
            for k, (lat, lon) in self.air_points.items():
                cell = nearest(lat, lon)
                if cell is None:
                    continue
                # The nearest land cell (by distance), else the nearest cell.
                best = None
                for dj in range(-LAND_RINGS, LAND_RINGS + 1):
                    for di in range(-LAND_RINGS, LAND_RINGS + 1):
                        j, i = cell[0] + dj, cell[1] + di
                        if wraps:
                            i %= cols
                        if not (0 <= j < rows and 0 <= i < cols):
                            continue
                        clat, clon = centre(j, i)
                        d = (clat - lat) ** 2 + ((clon - lon) * math.cos(math.radians(lat))) ** 2
                        if best is not None and d >= best[0]:
                            continue
                        own = self.ground_at(clat, clon)
                        if (own[1] if own is not None else self.coast.is_land(clat, clon)):
                            best = (d, (j, i))
                air[k] = best[1] if best else cell
                own = self.ground_at(*centre(*air[k]))
                self.set_shift(k, lat, lon, own[0] if own is not None else None)
        return wind, air

    def _add_regular(self, t, grid, fields, rate=None):
        """One hour from fields on a regular grid: "10u", "10v", "gust" (m/s), "2t" (K), "tcc", "low",
        "mid", "high" (% or 0-1); `rate` the rain mm an hour."""
        if "10u" not in fields or "10v" not in fields:
            return
        if self.cells is None:
            self.cells = self._regular_cells(grid, fields["10u"].shape)
        wind_cells, air_cells = self.cells
        u, v = fields["10u"], fields["10v"]
        gust = fields.get("gust")
        row = {}
        for k, cell in wind_cells.items():
            if cell is None or not np.isfinite(u[cell]) or not np.isfinite(v[cell]):
                row[k] = None
                continue
            speed = math.hypot(float(u[cell]), float(v[cell]))
            g = float(gust[cell]) if gust is not None and np.isfinite(gust[cell]) else speed
            row[k] = [round(float(u[cell]), 2), round(float(v[cell]), 2), round(max(g, speed), 2)]
        self.wind[int(t)] = row
        if not self.weather_name or "2t" not in fields:
            return
        # Each cloud field's scale once (ECMWF's own files give 0-1, the others %).
        scales = {name: (100 if float(np.nanmax(fields[name])) <= 1.5 else 1) for name in ("tcc", "low", "mid", "high") if name in fields}

        def percent(name, cell):
            values = fields.get(name)
            if values is None or not np.isfinite(values[cell]):
                return None
            return round(float(values[cell]) * scales[name])
        weather = {}
        for k, cell in air_cells.items():
            air = float(fields["2t"][cell]) - 273.15 + self.shift.get(k, 0.0)
            weather[k] = [round(air, 1), None if rate is None else round(max(float(rate[cell]), 0), 2),
                          percent("tcc", cell), percent("low", cell), percent("mid", cell), percent("high", cell)]
        self.air[int(t)] = weather

    # MARK: A model's own points, not on a grid (ECMWF's O1280)

    def _add_native(self, t, native):
        """One hour from a model's own points: native {"lat", "lon" (degrees), "values": {"10u",
        "10v", "gust", "2t" (K), "rate", "tcc", "low", "mid", "high"}}; the points chosen as Open-Meteo
        chooses them (checked on its answers, 18.10.2026): the wind at the nearest point by plain
        degrees (latitude and longitude difference, 60 of 60 places the same), the air at the
        nearest of the 4 that is land by the model's own ground (HSURF, the sea -999 there; most
        places the same, in steep places it may take another by its height)."""
        lats, lons, values = native["lat"], native["lon"], native["values"]
        if self.cells is None:
            from scipy.spatial import cKDTree
            tree = cKDTree(np.column_stack([lats, lons]))
            # The wind's point as Open-Meteo picks it on ECMWF's reduced Gaussian grid (its answers at 88
            # of 88 places, 19.10.2026; plain distance missed 6, the nearest row 33): a row guessed as if
            # the rows were evenly spaced (180 / (2 x 1280 + 0.5) degrees), that row and the next, in each
            # the two points either side in longitude; the nearest of those four by plain degrees.
            breaks = np.flatnonzero(np.diff(lats)) + 1
            starts = np.concatenate([[0], breaks])
            ends = np.concatenate([breaks, [len(lats)]])
            row_lats = lats[starts]
            half = len(row_lats) // 2
            step = 180 / (2 * half + 0.5)

            def open_meteo_point(lat, lon):
                y0 = int(math.floor(half - 1 - (lat - step / 2) / step))
                best = None
                for y in (y0, y0 + 1):
                    if not 0 <= y < len(row_lats):
                        continue
                    a, n = int(starts[y]), int(ends[y] - starts[y])
                    first = int(math.floor((lon % 360) / 360 * n))
                    for i in (first % n, (first + 1) % n):
                        d = (row_lats[y] - lat) ** 2 + (((lons[a + i] - lon) + 180) % 360 - 180) ** 2
                        if best is None or d < best[0]:
                            best = (d, a + i)
                return best[1] if best else int(tree.query([lat, lon])[1])
            wind = {k: open_meteo_point(lat, lon) for k, (lat, lon) in self.wind_points.items()}
            air = {}
            if self.weather_name:
                try:
                    import model_heights
                    ground = model_heights.read_hsurf(self.model_key).ravel()
                except Exception as error:
                    print(f"{self.set_name}: no ground for the land ({error})", flush=True)
                    ground = None
                raw = self.ground()
                for k, (lat, lon) in self.air_points.items():
                    _, near = tree.query([lat, lon], k=4)
                    land = [int(i) for i in near if ground is not None and ground[i] != 0]
                    air[k] = land[0] if land else int(near[0])
                    if raw:
                        v = float(raw[0].ravel()[air[k]])
                        self.set_shift(k, lat, lon, max(v, 0.0) if v > -500 else 0.0)
            self.cells = (wind, air)
        wind_cells, air_cells = self.cells
        u, v, gust = values.get("10u"), values.get("10v"), values.get("gust")
        if u is None or v is None:
            return
        row = {}
        for k, i in wind_cells.items():
            speed = math.hypot(float(u[i]), float(v[i]))
            g = float(gust[i]) if gust is not None else speed
            row[k] = [round(float(u[i]), 2), round(float(v[i]), 2), round(max(g, speed), 2)]
        self.wind[int(t)] = row
        if not self.weather_name or "2t" not in values:
            return
        scales = {name: (100 if float(np.nanmax(values[name])) <= 1.5 else 1) for name in ("tcc", "low", "mid", "high") if name in values}

        def percent(name, i):
            return round(float(values[name][i]) * scales[name]) if name in values else None
        self.air[int(t)] = {k: [round(float(values["2t"][i]) - 273.15 + self.shift.get(k, 0.0), 1),
                                None if "rate" not in values else round(max(float(values["rate"][i]), 0), 2),
                                percent("tcc", i), percent("low", i), percent("mid", i), percent("high", i)]
                            for k, i in air_cells.items()}

    def add_native(self, *args, **kwargs):
        if self.failed:
            return
        try:
            self._add_native(*args, **kwargs)
        except Exception as error:
            print(f"{self.set_name}: the spots' cells stopped ({error})", flush=True)
            self.failed = True

    # MARK: A model's own projection (the regional models, MET Nordic)

    def _add_placed(self, t, place, sample, east, north, gust, air=None, rain=None, cloud=None):
        """One hour on a model's own grid: `place(lon, lat)` -> its column, row (and weight), `sample(
        values, col, row)` its value there; the spot's nearest cell, the air at the spot's own."""
        if self.cells is None:
            def cell_of(points):
                out = {}
                for k, (lat, lon) in points.items():
                    found = place(np.array([lon]), np.array([lat]))
                    col, row = float(np.ravel(found[0])[0]), float(np.ravel(found[1])[0])
                    inside = len(found) < 3 or float(np.ravel(found[2])[0]) > 0
                    out[k] = (np.array([round(col)], dtype=float), np.array([round(row)], dtype=float)) if inside else None
                return out
            self.cells = (cell_of(self.wind_points), cell_of(self.air_points))
            if self.weather_name:
                for k, (lat, lon) in self.air_points.items():
                    own = self.ground_at(lat, lon)
                    self.set_shift(k, lat, lon, own[0] if own is not None else None)
        wind_cells, air_cells = self.cells

        def at(values, cell):
            if values is None or cell is None:
                return None
            value = float(np.ravel(sample(values, *cell))[0])
            return value if math.isfinite(value) else None
        row = {}
        for k, cell in (wind_cells.items() if self.set_name else ()):
            e, n, g = at(east, cell), at(north, cell), at(gust, cell)
            if e is None or n is None:
                row[k] = None
                continue
            speed = math.hypot(e, n)
            row[k] = [round(e, 2), round(n, 2), round(max(g if g is not None else speed, speed), 2)]
        self.wind[int(t)] = row
        if air is not None and self.weather_name:
            self.air[int(t)] = {k: [None if at(air, c) is None else round(at(air, c) + self.shift.get(k, 0.0), 1),
                                    None if at(rain, c) is None else round(max(at(rain, c), 0), 2),
                                    None if at(cloud, c) is None else round(at(cloud, c) * (100 if at(cloud, c) <= 1.5 else 1)),
                                    None, None, None] for k, c in air_cells.items()}

    # MARK: Safe to call from the tile builders: a failure stops the spots' cells, never the tiles.

    def add_regular(self, *args, **kwargs):
        if self.failed:
            return
        try:
            self._add_regular(*args, **kwargs)
        except Exception as error:
            print(f"{self.set_name}: the spots' cells stopped ({error})", flush=True)
            self.failed = True

    def add_placed(self, *args, **kwargs):
        if self.failed:
            return
        try:
            self._add_placed(*args, **kwargs)
        except Exception as error:
            print(f"{self.set_name}: the spots' cells stopped ({error})", flush=True)
            self.failed = True

    # MARK: Publishing

    def publish(self, run_name, local=None, keep_after=False):
        """The wind's and the weather's files (each with the hours before this run's first from the
        file before, a day and a half, as Open-Meteo's answers have the day before; with
        `keep_after`, also its hours after this run's last: a short run, ECMWF's 06/18 UTC 90 hours,
        over the long one's, as Open-Meteo keeps its time series)."""
        if self.failed:
            return
        for name, rows in ((self.set_name, self.wind), (self.weather_name, self.air)):
            if not name or not rows:
                continue
            times = sorted(rows)
            keys = sorted({k for r in rows.values() for k in r})
            series = {k: [rows[t].get(k) for t in times] for k in keys}
            before = None if local else spot_points.published(name)
            # Never an older run over a newer one (19.10.2026: a late rerun of ECMWF's 12Z put it
            # back over the 18Z short run at the spots): the older run only adds its hours after
            # the newer one's last, the file keeping the newer run's name.
            mine, theirs = run_time(run_name), run_time(before.get("run")) if before else None
            if before and mine and theirs and theirs > mine:
                old_times = before.get("times", [])
                later = [i for i, t in enumerate(times) if not old_times or t > old_times[-1]]
                if not later:
                    print(f"{name}: {before.get('run')} is newer than {run_name}, kept", flush=True)
                    continue
                merged = {}
                for k in set(before.get("points", {})) | set(series):
                    old = before.get("points", {}).get(k) or [None] * len(old_times)
                    new = series.get(k)
                    merged[k] = old + ([new[i] for i in later] if new else [None] * len(later))
                body = json.dumps({"set": name, "run": before.get("run"), "source": "grid",
                                   "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                                   "times": old_times + [times[i] for i in later], "points": merged},
                                  separators=(",", ":")).encode()
                world_tiles.upload(f"points/{name}.json", body)
                print(f"{name}: {before.get('run')} kept, {len(later)} later hours from {run_name}", flush=True)
                continue
            if before and times:
                keep = [i for i, t in enumerate(before.get("times", [])) if times[0] - spot_points.KEEP_BEFORE <= t < times[0]]
                if keep:
                    old_times = [before["times"][i] for i in keep]
                    for k in series:
                        old = before.get("points", {}).get(k)
                        series[k] = ([old[i] for i in keep] if old else [None] * len(keep)) + series[k]
                    times = old_times + times
            if before and times and keep_after:
                later = [i for i, t in enumerate(before.get("times", [])) if t > times[-1]]
                if later:
                    for k in series:
                        old = before.get("points", {}).get(k)
                        series[k] = series[k] + ([old[i] for i in later] if old else [None] * len(later))
                    times = times + [before["times"][i] for i in later]
            body = json.dumps({"set": name, "run": run_name, "source": "grid",
                               "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                               "times": times, "points": series}, separators=(",", ":")).encode()
            if local:
                Path(local).mkdir(parents=True, exist_ok=True)
                (Path(local) / f"{name}.json").write_bytes(body)
            else:
                world_tiles.upload(f"points/{name}.json", body)
            print(f"{name}: {len(series)} points from the model's own cells, {len(times)} hours", flush=True)
