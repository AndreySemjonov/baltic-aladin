"""The spots' values from a model's own grid (the owner, 18.10.2026: our wind at the spots read lower
and our air warmer than Open-Meteo's for the same models, by 0.2-0.4 m/s and 0.3-1 °C: our map
pixels blend a cell with its neighbours, at the coast land into the sea's wind and sea into the land's
air; Open-Meteo reads the model's own cell): while a model's run is made into tiles, each spot's wind
from the cell nearest its point for that model (its csPoints: Open-Meteo's cell_selection=nearest)
and its air, rain and clouds from the nearest cell whose centre is on land (cell_selection=land; our
coastline tiles say where land is), written as spot_points.py's files (points/<set>.json: [east,
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

    # MARK: Regular grids (ECMWF, GFS, ICON, GDPS, ICON-EU)

    def _regular_cells(self, grid, shape):
        rows, cols = shape
        lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
        dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
        dlon = grid["iDirectionIncrementInDegrees"]
        wraps = abs(cols * dlon - 360) < 1.5 * dlon

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
                        if (best is None or d < best[0]) and self.coast.is_land(clat, clon):
                            best = (d, (j, i))
                air[k] = best[1] if best else cell
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
            air = float(fields["2t"][cell]) - 273.15
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
            wind = {k: int(tree.query([lat, lon])[1]) for k, (lat, lon) in self.wind_points.items()}
            air = {}
            if self.weather_name:
                try:
                    import model_heights
                    ground = model_heights.read_hsurf(self.model_key).ravel()
                except Exception as error:
                    print(f"{self.set_name}: no ground for the land ({error})", flush=True)
                    ground = None
                for k, (lat, lon) in self.air_points.items():
                    _, near = tree.query([lat, lon], k=4)
                    land = [int(i) for i in near if ground is not None and ground[i] != 0]
                    air[k] = land[0] if land else int(near[0])
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
        self.air[int(t)] = {k: [round(float(values["2t"][i]) - 273.15, 1),
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
        wind_cells, air_cells = self.cells

        def at(values, cell):
            if values is None or cell is None:
                return None
            value = float(np.ravel(sample(values, *cell))[0])
            return value if math.isfinite(value) else None
        row = {}
        for k, cell in wind_cells.items():
            e, n, g = at(east, cell), at(north, cell), at(gust, cell)
            if e is None or n is None:
                row[k] = None
                continue
            speed = math.hypot(e, n)
            row[k] = [round(e, 2), round(n, 2), round(max(g if g is not None else speed, speed), 2)]
        self.wind[int(t)] = row
        if air is not None and self.weather_name:
            self.air[int(t)] = {k: [None if at(air, c) is None else round(at(air, c), 1),
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

    def publish(self, run_name, local=None):
        """The wind's and the weather's files (each with the hours before this run's first from the
        file before, a day and a half, as Open-Meteo's answers have the day before)."""
        if self.failed:
            return
        for name, rows in ((self.set_name, self.wind), (self.weather_name, self.air)):
            if not name or not rows:
                continue
            times = sorted(rows)
            keys = sorted({k for r in rows.values() for k in r})
            series = {k: [rows[t].get(k) for t in times] for k in keys}
            before = None if local else spot_points.published(name)
            if before and times:
                keep = [i for i, t in enumerate(before.get("times", [])) if times[0] - spot_points.KEEP_BEFORE <= t < times[0]]
                if keep:
                    old_times = [before["times"][i] for i in keep]
                    for k in series:
                        old = before.get("points", {}).get(k)
                        series[k] = ([old[i] for i in keep] if old else [None] * len(keep)) + series[k]
                    times = old_times + times
            body = json.dumps({"set": name, "run": run_name, "source": "grid",
                               "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                               "times": times, "points": series}, separators=(",", ":")).encode()
            if local:
                Path(local).mkdir(parents=True, exist_ok=True)
                (Path(local) / f"{name}.json").write_bytes(body)
            else:
                world_tiles.upload(f"points/{name}.json", body)
            print(f"{name}: {len(series)} points from the model's own cells, {len(times)} hours", flush=True)
