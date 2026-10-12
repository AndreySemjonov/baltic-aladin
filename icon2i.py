"""ItaliaMeteo's ICON-2I (2.2 km; open data on MeteoHub, no key: https://meteohub.agenziaitaliameteo.it/)
for the regional tiles (regional_tiles.py): Italy, Sardinia, Sicily, the Adriatic, Corsica and
the Alps' south (33.7-48.9N, 3-22E), runs at 00 and 12 UTC to 72 hours. Each field is one GRIB2
file of all its hours; its messages after the first are the same size (simple packing), so only
the hours wanted are read, by range. How much it counts: 1 inside, falling to 0 over the last 50 km to its area's
edge.
"""
import re
import struct
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import eccodes
import numpy as np

BASE = "https://meteohub.agenziaitaliameteo.it/nwp/ICON-2I_SURFACE_PRESSURE_LEVELS/"
FILE = "{run}/{var}/ICON_2I_SURFACE_PRESSURE_LEVELS_{run}_heightAboveGround-10.grib"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"
NI, NJ = 761, 761
SOUTH, NORTH, WEST, EAST = 33.7, 48.9, 3.0, 22.0
LAST = 72
FADE_KM = 50


def _get(url, start=None, end=None, timeout=120):
    headers = {"User-Agent": USER_AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as reply:
                return reply.read()
        except OSError:
            if attempt == 2:
                raise


def _span(url, hour):
    """The byte range of an hour's message: the hours after the first are the same size (the
    first can be shorter: a constant field, as the precipitation's and the gust's at hour 0)."""
    first = struct.unpack(">Q", _get(url, 0, 15, timeout=60)[8:16])[0]
    if hour == 0:
        return 0, first - 1
    length = struct.unpack(">Q", _get(url, first, first + 15, timeout=60)[8:16])[0]
    start = first + (hour - 1) * length
    return start, start + length - 1


class Model:
    def __init__(self):
        runs = sorted(set(re.findall(r'href="(\d{10})/"', _get(BASE).decode())))
        self.run = None
        for run in reversed(runs):
            listing = _get(f"{BASE}{run}/VMAX_10M/").decode()
            # The listing shortens long names ("...heig..&gt;"): the size after the link.
            sizes = [int(n) for n in re.findall(r'heightAboveGround-10\.grib">[^<]*</a>\s+\S+\s+\S+\s+(\d+)', listing)]
            # Complete: all its hours (0-72) out (about 85 MB).
            if sizes and sizes[0] > 70_000_000:
                self.run = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
                break
        if self.run is None:
            raise RuntimeError("no complete ICON-2I run")

    def _hour(self, var, hour):
        """One hour's field (rows from the south), read by its message's range."""
        url = BASE + FILE.format(run=self.run.strftime("%Y%m%d%H"), var=var)
        data = _get(url, *_span(url, hour))
        if data[:4] != b"GRIB":
            raise RuntimeError(f"{var} hour {hour}: not a message at its place")
        handle = eccodes.codes_new_from_message(data)
        try:
            step = eccodes.codes_get(handle, "endStep")
            if step != hour:
                raise RuntimeError(f"{var} hour {hour}: step {step}")
            return eccodes.codes_get_values(handle).astype(np.float32).reshape(NJ, NI)
        finally:
            eccodes.codes_release(handle)

    def winds(self, times):
        """{unix time: (east, north, gust)} for the asked times the run has (its hours 1-72):
        downloads side by side, decoded one at a time."""
        start = int(self.run.timestamp())
        wanted = {round((t - start) / 3600): t for t in times if 3600 <= t - start <= LAST * 3600}
        keys = [(var, hour) for hour in wanted for var in ("U_10M", "V_10M", "VMAX_10M")]

        def fetch(key):
            var, hour = key
            url = BASE + FILE.format(run=self.run.strftime("%Y%m%d%H"), var=var)
            try:
                return _get(url, *_span(url, hour))
            except Exception as error:
                print(f"ICON-2I {var} {hour}: {error}", flush=True)
                return None

        with ThreadPoolExecutor(8) as pool:
            raw = dict(zip(keys, pool.map(fetch, keys)))
        fields = {}
        for (var, hour), data in raw.items():
            if not data or data[:4] != b"GRIB":
                continue
            handle = eccodes.codes_new_from_message(data)
            try:
                if eccodes.codes_get(handle, "endStep") == hour:
                    fields[(var, hour)] = eccodes.codes_get_values(handle).astype(np.float32).reshape(NJ, NI)
            finally:
                eccodes.codes_release(handle)
        out = {}
        for hour, t in wanted.items():
            if all((var, hour) in fields for var in ("U_10M", "V_10M", "VMAX_10M")):
                out[t] = (fields[("U_10M", hour)], fields[("V_10M", hour)], fields[("VMAX_10M", hour)])
        return out

    def _hours(self, var, level, hours):
        """{hour: field} of one field's file (its messages the same size) at the hours."""
        name = self.run.strftime("%Y%m%d%H")
        url = f"{BASE}{name}/{var}/ICON_2I_SURFACE_PRESSURE_LEVELS_{name}_{level}.grib"
        first = struct.unpack(">Q", _get(url, 0, 15, timeout=60)[8:16])[0]
        length = struct.unpack(">Q", _get(url, first, first + 15, timeout=60)[8:16])[0]

        def span(h):
            return (0, first - 1) if h == 0 else (first + (h - 1) * length, first + h * length - 1)
        with ThreadPoolExecutor(8) as pool:
            raw = dict(zip(hours, pool.map(lambda h: _get(url, *span(h)), hours)))
        out = {}
        for h, data in raw.items():
            if data[:4] != b"GRIB":
                continue
            handle = eccodes.codes_new_from_message(data)
            try:
                if eccodes.codes_get(handle, "endStep") == h:
                    out[h] = eccodes.codes_get_values(handle).astype(np.float32).reshape(NJ, NI)
            finally:
                eccodes.codes_release(handle)
        return out

    def weather(self, times):
        """{unix time: {air, rh, wind, rain (mm/h), cloud}} for the asked times in the run (the
        humidity from the dew point; the precipitation summed from the start)."""
        start = int(self.run.timestamp())
        hours = sorted({round((t - start) / 3600) for t in times if 3600 <= t - start <= LAST * 3600})
        if not hours:
            return {}
        need = sorted(set(hours) | {h - 1 for h in hours})
        air = self._hours("T_2M", "heightAboveGround-2", hours)
        dew = self._hours("TD_2M", "heightAboveGround-2", hours)
        u = self._hours("U_10M", "heightAboveGround-10", hours)
        v = self._hours("V_10M", "heightAboveGround-10", hours)
        cloud = self._hours("CLCT", "surface-0", hours)
        total = self._hours("TOT_PREC", "surface-0", need)
        out = {}
        for h in hours:
            if not all(h in f for f in (air, dew, u, v, cloud)) or h not in total or h - 1 not in total:
                continue
            c, d = air[h] - 273.15, dew[h] - 273.15
            rh = np.clip(np.exp(17.625 * d / (243.04 + d)) / np.exp(17.625 * c / (243.04 + c)), 0, 1)
            out[start + h * 3600] = {"air": c, "rh": rh, "wind": np.hypot(u[h], v[h]),
                                     "rain": np.maximum(total[h] - total[h - 1], 0), "cloud": np.clip(cloud[h] / 100, 0, 1)}
        return out

    def place(self, lon, lat):
        col = (lon - WEST) / (EAST - WEST) * (NI - 1)
        row = (lat - SOUTH) / (NORTH - SOUTH) * (NJ - 1)
        inside = (col >= 0) & (row >= 0) & (col <= NI - 1) & (row <= NJ - 1)
        km = np.minimum.reduce([(lat - SOUTH) * 111, (NORTH - lat) * 111,
                                (lon - WEST) * 111 * np.cos(np.radians(lat)), (EAST - lon) * 111 * np.cos(np.radians(lat))])
        return col, row, np.where(inside, np.clip(km / FADE_KM, 0, 1), 0)

    @staticmethod
    def sample(grid, col, row):
        c = np.clip(col, 0, NI - 1.001)
        r = np.clip(row, 0, NJ - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        top = grid[r0, c0] * (1 - fc) + grid[r0, c0 + 1] * fc
        bottom = grid[r0 + 1, c0] * (1 - fc) + grid[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr
