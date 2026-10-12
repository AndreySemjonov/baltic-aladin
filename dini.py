"""DMI's HARMONIE DINI (2 km, open data, CC BY 4.0: https://opendatadocs.dmi.govcloud.dk/) for the
regional tiles (regional_tiles.py): over the British Isles, the North Sea, Denmark and the Channel
(its whole domain runs from Greenland to Russia; only the rows crossing that band are read, with
range requests, as dmi_map.py does for the Baltic). The newest complete run, its 10 m wind hour by
hour, and how much it counts at a place: 1 inside the band, falling to 0 over its last 50 km.
"""
from concurrent.futures import ThreadPoolExecutor

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
