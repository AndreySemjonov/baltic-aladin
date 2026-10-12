"""DMI's HARMONIE DINI (2 km, open data, CC BY 4.0: https://opendatadocs.dmi.govcloud.dk/) for the
regional tiles (regional_tiles.py): over the British Isles, the North Sea, Denmark and the Channel
(its whole domain runs from Greenland to Russia; only the rows crossing that band are read, with
range requests, as dmi_map.py does for the Baltic). The newest complete run, its 10 m wind hour by
hour, and how much it counts at a place: 1 inside the band, falling to 0 over its last 50 km.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import numpy as np

import aladin_map
import dmi_map

# The band read (degrees): the British Isles to Denmark, the Channel to southern Norway.
SOUTH, NORTH, WEST, EAST = 46.0, 64.0, -14.0, 20.0
FADE_KM = 50


class Model:
    """The newest run over the band: its rows and columns on DINI's grid and its files."""

    def __init__(self):
        self.run, self.files = dmi_map.newest_run()
        lat = np.linspace(SOUTH, NORTH, 60)
        lon = np.linspace(WEST, EAST, 80)
        glat, glon = np.meshgrid(lat, lon, indexing="ij")
        fi, fj = aladin_map.lambert(glat, glon, dmi_map.GRID)
        nx, ny = dmi_map.GRID["Nx"], dmi_map.GRID["Ny"]
        self.c0, self.c1 = max(int(np.floor(fi.min())), 0), min(int(np.ceil(fi.max())) + 1, nx - 1)
        self.r0, self.r1 = max(int(np.floor(fj.min())), 0), min(int(np.ceil(fj.max())) + 1, ny - 1)
        self.width, self.height = self.c1 - self.c0 + 1, self.r1 - self.r0 + 1
        newest = max(self.files)
        self.hint = {name: info["offset"] for name, info in dmi_map.locate(self.files[newest], {}).items()}

    def winds(self, times):
        """{unix time: (east, north, gust)} on the band's grid for the valid times the run has."""
        wanted = [t for t in self.files if int(t.timestamp()) in set(times)]

        def fetch(valid):
            url = self.files[valid]
            found = dmi_map.locate(url, self.hint)
            if "speed" not in found or "direction" not in found:
                return valid, None
            part = {name: dmi_map.rows(url, info, self.r0, self.r1)[:, self.c0:self.c1 + 1] for name, info in found.items()}
            rad = np.radians(part["direction"])
            east, north = -part["speed"] * np.sin(rad), -part["speed"] * np.cos(rad)
            return valid, (east, north, part.get("gust", part["speed"]))

        with ThreadPoolExecutor(8) as pool:
            return {int(valid.timestamp()): fields for valid, fields in pool.map(fetch, wanted) if fields is not None}

    WEATHER = {"air": (0, 0, 0, 103, 2), "rh": (0, 1, 1, 103, 2), "wind": (0, 2, 1, 103, 10),
               "rate": (0, 1, 52, 1, 4294967295), "cloud": (0, 6, 32, 103, 0)}

    def _locate(self, url):
        """The weather fields' headers in one file: the first message of each (the later 2 m
        temperatures are the period's highest and lowest)."""
        found, offset, wanted = {}, 0, {v: k for k, v in self.WEATHER.items()}
        while len(found) < len(self.WEATHER):
            try:
                info = dmi_map.header(url, offset)
            except Exception:
                break
            name = wanted.get(dmi_map.key(info))
            if name and name not in found:
                found[name] = dict(info, offset=offset)
            offset += info["total"]
        return found

    def weather(self, times):
        """{unix time: {air, rh, wind, rain (mm/h), cloud}} for the asked times. The precipitation
        field (0, 1, 52: "rate" by its code) holds the run's total so far in mm (it only grows: 59 mm
        at +24 h, 90 at +40 h; read as a rate x 3600 it drew a block of rain over Germany and
        Poland, 12.10.2026): each hour's from the hour before's."""
        wanted = [t for t in self.files if int(t.timestamp()) in set(times)]
        if not wanted:
            return {}
        layout = self._locate(self.files[wanted[0]])
        hour = timedelta(hours=1)
        # The hours before (their totals only).
        before = sorted({t - hour for t in wanted if t - hour in self.files} - set(wanted))

        def total(valid):
            url = self.files[valid]
            info = layout.get("rate")
            try:
                if info is None or dmi_map.key(dmi_map.header(url, info["offset"])) != self.WEATHER["rate"]:
                    info = self._locate(url).get("rate")
                return valid, None if info is None else dmi_map.rows(url, info, self.r0, self.r1)[:, self.c0:self.c1 + 1]
            except Exception:
                return valid, None

        def fetch(valid):
            url = self.files[valid]
            found = dict(layout)
            # The first file's layout serves the others (checked; walked again if it moved).
            try:
                for name, info in found.items():
                    if dmi_map.key(dmi_map.header(url, info["offset"])) != self.WEATHER[name]:
                        raise ValueError(name)
            except Exception:
                found = self._locate(url)
            if not {"air", "rh", "wind", "rate", "cloud"} <= found.keys():
                return valid, None
            part = {name: dmi_map.rows(url, info, self.r0, self.r1)[:, self.c0:self.c1 + 1] for name, info in found.items()}
            air = part["air"] - 273.15 if np.nanmean(part["air"]) > 150 else part["air"]
            rh = part["rh"] / 100 if np.nanmax(part["rh"]) > 1.5 else part["rh"]
            cloud = part["cloud"] / 100 if np.nanmax(part["cloud"]) > 1.5 else part["cloud"]
            return valid, {"air": air, "rh": np.clip(rh, 0, 1), "wind": part["wind"],
                           "total": part["rate"], "cloud": np.clip(cloud, 0, 1)}

        with ThreadPoolExecutor(8) as pool:
            fields = {valid: data for valid, data in pool.map(fetch, wanted) if data is not None}
            totals = {valid: data for valid, data in pool.map(total, before) if data is not None}
        totals.update({valid: data["total"] for valid, data in fields.items()})
        out = {}
        for valid, data in fields.items():
            previous = totals.get(valid - hour)
            # The run's first hour: its total is the hour's.
            amount = data.pop("total") - previous if previous is not None else (data.pop("total") if valid - hour == self.run else None)
            if amount is None:
                continue
            data["rain"] = np.maximum(amount, 0)
            out[int(valid.timestamp())] = data
        return out

    def place(self, lon, lat):
        """Fractional column and row on the band's grid, how much DINI counts there (by the
        distance to the band's edge), and whether the point lies on it."""
        fi, fj = aladin_map.lambert(lat, lon, dmi_map.GRID)
        col, row = fi - self.c0, fj - self.r0
        inside = (col >= 0) & (row >= 0) & (col <= self.width - 1) & (row <= self.height - 1)
        # The band's edge in degrees (its lat-lon box), about 111 km a degree north and east.
        km = np.minimum.reduce([(lat - SOUTH) * 111, (NORTH - lat) * 111,
                                (lon - WEST) * 111 * np.cos(np.radians(lat)), (EAST - lon) * 111 * np.cos(np.radians(lat))])
        weight = np.where(inside, np.clip(km / FADE_KM, 0, 1), 0)
        return col, row, weight

    def sample(self, grid, col, row):
        c = np.clip(col, 0, self.width - 1.001)
        r = np.clip(row, 0, self.height - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        top = grid[r0, c0] * (1 - fc) + grid[r0, c0 + 1] * fc
        bottom = grid[r0 + 1, c0] * (1 - fc) + grid[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr
