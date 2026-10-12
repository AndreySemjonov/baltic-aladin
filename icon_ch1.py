"""MeteoSwiss's ICON-CH1 (1 km, the control run of ICON-CH1-EPS; open data, "open use, give the
source": https://opendatadocs.meteoswiss.ch/) for the regional tiles (regional_tiles.py):
Switzerland, the Alps, northern Italy with Lake Garda and Como, south-eastern France and southern
Germany. Its fields are on ICON's own triangular grid (about 1.15 million cells; their places in
the collection's "horizontal constants"), one file a field and an hour, found through MeteoSwiss's
STAC search; a place takes its nearest cell. How much it counts: 1 inside, falling to 0 over the
last 50 km to its area's edge.
"""
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import eccodes
import numpy as np
from scipy.spatial import cKDTree

SEARCH = "https://data.geo.admin.ch/api/stac/v1/search"
COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch1"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"
LAST = 33
FADE_KM = 50
# The mask grid for the fade (0.05 degrees, about 5 km).
MASK_STEP = 0.05


def _get(url, data=None, timeout=120):
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                return reply.read()
        except OSError:
            if attempt == 2:
                raise


def _search(body):
    items, url, payload = [], SEARCH, json.dumps(body).encode()
    while url:
        page = json.loads(_get(url, payload))
        items += page.get("features", [])
        nxt = [link for link in page.get("links", []) if link.get("rel") == "next"]
        if not nxt:
            break
        url, payload = nxt[0]["href"], json.dumps(nxt[0].get("body", body)).encode() if nxt[0].get("method") == "POST" else None
    return items


def _hours(horizon):
    m = re.match(r"P(\d+)DT(\d+)H", horizon)
    return int(m.group(1)) * 24 + int(m.group(2)) if m else -1


def _values(data):
    """Every message of a GRIB2 file's bytes, by short name (the first of each), split in memory."""
    import struct
    out, at = {}, 0
    while at + 16 <= len(data) and data[at:at + 4] == b"GRIB":
        length = struct.unpack(">Q", data[at + 8:at + 16])[0]
        handle = eccodes.codes_new_from_message(data[at:at + length])
        try:
            out.setdefault(eccodes.codes_get(handle, "shortName"), eccodes.codes_get_values(handle).astype(np.float32))
        finally:
            eccodes.codes_release(handle)
        at += length
    return out


class Model:
    """The newest control run whose last hour is out."""

    def __init__(self):
        body = {"collections": [COLLECTION], "forecast:variable": "VMAX_10M", "forecast:perturbed": False,
                "forecast:horizon": f"P1DT{LAST - 24:02d}H00M00S", "limit": 20}
        runs = sorted({item["properties"]["forecast:reference_datetime"] for item in _search(body)})
        if not runs:
            raise RuntimeError("no complete ICON-CH1 run")
        self.run = datetime.strptime(runs[-1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        # The cells' places and the tree to find a place's nearest.
        collection = json.loads(_get(f"https://data.geo.admin.ch/api/stac/v1/collections/{COLLECTION}"))
        constants = _values(_get(collection["assets"]["horizontal_constants_icon-ch1-eps.grib2"]["href"], timeout=300))
        lon, lat = constants["tlon"].astype(np.float64), constants["tlat"].astype(np.float64)
        self.tree = cKDTree(self._xyz(lon, lat))
        # The area as a mask on a coarse grid, and its distance to the edge (a chamfer, two passes).
        self.lon0, self.lat0 = lon.min() - 1, lat.min() - 1
        self.nx, self.ny = int((lon.max() + 1 - self.lon0) / MASK_STEP) + 1, int((lat.max() + 1 - self.lat0) / MASK_STEP) + 1
        mask = np.zeros((self.ny, self.nx), dtype=bool)
        mask[((lat - self.lat0) / MASK_STEP).astype(int), ((lon - self.lon0) / MASK_STEP).astype(int)] = True
        d = np.where(mask, 1e4, 0.0).astype(np.float32)
        for _ in range(2):
            for j in range(1, self.ny):
                d[j] = np.minimum(d[j], d[j - 1] + 1)
            for j in range(self.ny - 2, -1, -1):
                d[j] = np.minimum(d[j], d[j + 1] + 1)
            for i in range(1, self.nx):
                d[:, i] = np.minimum(d[:, i], d[:, i - 1] + 1)
            for i in range(self.nx - 2, -1, -1):
                d[:, i] = np.minimum(d[:, i], d[:, i + 1] + 1)
        self.edge_km = d * MASK_STEP * 111 * 0.75  # about 4 km a cell (east-west shorter at 46N)

    @staticmethod
    def _xyz(lon, lat):
        lo, la = np.radians(lon), np.radians(lat)
        return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)

    def winds(self, times):
        """{unix time: (east, north, gust)} (cell arrays) for the asked times the run has."""
        start = int(self.run.timestamp())
        wanted = {round((t - start) / 3600): t for t in times if 0 < t - start <= LAST * 3600}
        run = self.run.strftime("%Y-%m-%dT%H:%M:%SZ")
        hrefs = {}
        for name, variable in (("u", "U_10M"), ("v", "V_10M"), ("gust", "VMAX_10M")):
            body = {"collections": [COLLECTION], "forecast:variable": variable, "forecast:perturbed": False,
                    "forecast:reference_datetime": run, "limit": 100}
            for item in _search(body):
                hour = _hours(item["properties"]["forecast:horizon"])
                if hour in wanted:
                    hrefs[(name, hour)] = next(iter(item["assets"].values()))["href"]
        with ThreadPoolExecutor(8) as pool:
            raw = dict(zip(hrefs, pool.map(lambda key: _get(hrefs[key], timeout=180), hrefs)))
        fields = {key: next(iter(_values(data).values())) for key, data in raw.items()}
        out = {}
        for hour, t in wanted.items():
            if all((name, hour) in fields for name in ("u", "v", "gust")):
                out[t] = (fields[("u", hour)], fields[("v", hour)], fields[("gust", hour)])
        return out

    def _fields(self, variables, hours):
        """{(name, hour): cells} for the control run's variables at the hours."""
        run = self.run.strftime("%Y-%m-%dT%H:%M:%SZ")
        hrefs = {}
        for name, variable in variables:
            body = {"collections": [COLLECTION], "forecast:variable": variable, "forecast:perturbed": False,
                    "forecast:reference_datetime": run, "limit": 100}
            for item in _search(body):
                hour = _hours(item["properties"]["forecast:horizon"])
                if hour in hours:
                    hrefs[(name, hour)] = next(iter(item["assets"].values()))["href"]
        with ThreadPoolExecutor(8) as pool:
            raw = dict(zip(hrefs, pool.map(lambda key: _get(hrefs[key], timeout=180), hrefs)))
        return {key: next(iter(_values(data).values())) for key, data in raw.items()}

    def weather(self, times):
        """{unix time: {air, rh, wind, rain (mm/h), cloud}} (cells) for the asked times in the run (the
        humidity from the dew point: the open data has no RELHUM_2M)."""
        start = int(self.run.timestamp())
        hours = {round((t - start) / 3600) for t in times if 0 < t - start <= LAST * 3600}
        need = hours | {h - 1 for h in hours}
        f = self._fields((("air", "T_2M"), ("dew", "TD_2M"), ("u", "U_10M"), ("v", "V_10M"), ("cloud", "CLCT")), hours)
        f.update(self._fields((("total", "TOT_PREC"),), need))
        out = {}
        for h in sorted(hours):
            keys = [("air", h), ("dew", h), ("u", h), ("v", h), ("cloud", h), ("total", h), ("total", h - 1)]
            if all(k in f for k in keys):
                c, d = f[("air", h)] - 273.15, f[("dew", h)] - 273.15
                rh = np.clip(np.exp(17.625 * d / (243.04 + d)) / np.exp(17.625 * c / (243.04 + c)), 0, 1)
                out[start + h * 3600] = {"air": c, "rh": rh,
                                         "wind": np.hypot(f[("u", h)], f[("v", h)]),
                                         "rain": np.maximum(f[("total", h)] - f[("total", h - 1)], 0),
                                         "cloud": np.clip(f[("cloud", h)] / 100, 0, 1)}
        return out

    def place(self, lon, lat):
        """A place's nearest cell (as `col`; `row` unused), and how much ICON-CH1 counts there:
        none farther than 2 km from a cell, else by the area's edge."""
        # Far from the area (most tiles): nothing, without asking the tree.
        if not ((lon > self.lon0) & (lon < self.lon0 + self.nx * MASK_STEP) & (lat > self.lat0)
                & (lat < self.lat0 + self.ny * MASK_STEP)).any():
            return np.zeros(lon.shape, dtype=int), None, np.zeros(lon.shape)
        distance, index = self.tree.query(self._xyz(lon.ravel(), lat.ravel()))
        km = distance.reshape(lon.shape) * 6371
        j = np.clip(((lat - self.lat0) / MASK_STEP).astype(int), 0, self.ny - 1)
        i = np.clip(((lon - self.lon0) / MASK_STEP).astype(int), 0, self.nx - 1)
        weight = np.where(km <= 2, np.clip(self.edge_km[j, i] / FADE_KM, 0, 1), 0)
        return index.reshape(lon.shape), None, weight

    @staticmethod
    def sample(cells, index, _row):
        return cells[index]
