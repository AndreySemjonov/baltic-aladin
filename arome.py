"""Météo-France's AROME (0.01 degrees, about 1.3 km; open data, Etalab 2.0 / CC BY compatible:
https://meteo.data.gouv.fr/) for the regional tiles (regional_tiles.py): France, the Channel, the
Bay of Biscay's north, Benelux, western Germany, Switzerland and northern Italy (37.5-55.4N,
12W-16E). Public files, no key (meteofrance-pnt on OVH's object store): one GRIB2 file an hour
ahead (SP1: 2 m air and humidity, then the 10 m wind's parts and the hour's gust's parts), of which
only the four wind messages are read (their bytes, after the first two's lengths).
"""
import struct
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import eccodes
import numpy as np

BASE = "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/"
FILE = "{run}:00:00Z/arome/001/SP1/arome__001__SP1__{hour:02d}H__{run}:00:00Z.grib2"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"
# The grid: rows from the north (55.4N), columns from 12W, every 0.01 degrees.
NI, NJ, LAT_TOP, LON0, STEP = 2801, 1791, 55.4, -12.0, 0.01
SOUTH, NORTH, WEST, EAST = 37.5, 55.4, -12.0, 16.0
LAST = 42
FADE_KM = 50
# The model's own area is slanted inside the grid: its corners carry no data (the bitmap; ecCodes
# gives them 9999, drawn as storms on 12.10.2026). Where there is data, on a coarse grid (every
# 5th cell, about 4.5 km), and its distance to the area's edge; learnt from the first field read.
COARSE = 5
COARSE_KM = 4.5
_edge_km = None


def _values(handle):
    """A message's values (rows from the south as the others'), NaN where it has none."""
    values = eccodes.codes_get_values(handle).astype(np.float32)
    if eccodes.codes_get(handle, "bitmapPresent"):
        values[values == eccodes.codes_get(handle, "missingValue")] = np.nan
    grid = values.reshape(NJ, NI)[::-1]
    _learn(grid)
    return grid


def _learn(grid):
    """The area's edge distance from a field's cells with data (once)."""
    global _edge_km
    if _edge_km is not None:
        return
    valid = np.isfinite(grid[::COARSE, ::COARSE])
    d = np.where(valid, 1e4, 0.0).astype(np.float32)
    d[0, :] = d[-1, :] = 0
    d[:, 0] = d[:, -1] = 0
    ny, nx = d.shape
    for _ in range(2):
        for j in range(1, ny):
            d[j] = np.minimum(d[j], d[j - 1] + 1)
        for j in range(ny - 2, -1, -1):
            d[j] = np.minimum(d[j], d[j + 1] + 1)
        for i in range(1, nx):
            d[:, i] = np.minimum(d[:, i], d[:, i - 1] + 1)
        for i in range(nx - 2, -1, -1):
            d[:, i] = np.minimum(d[:, i], d[:, i + 1] + 1)
    _edge_km = d * COARSE_KM


def get(url, start=None, end=None, timeout=300):
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


def exists(url):
    try:
        get(url, 0, 15, timeout=30)
        return True
    except OSError:
        return False


def newest_run():
    """The newest run (every 3 hours) whose hour LAST is out."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 3 * 3, minute=0, second=0, microsecond=0)
    for back in range(8):
        run = start - timedelta(hours=3 * back)
        if exists(BASE + FILE.format(run=run.strftime("%Y-%m-%dT%H"), hour=LAST)):
            return run
    return None


class Model:
    def __init__(self):
        self.run = newest_run()
        if self.run is None:
            raise RuntimeError("no complete AROME run")

    def _bytes(self, hour):
        """One hour's four wind messages (their bytes, after the first two's lengths)."""
        url = BASE + FILE.format(run=self.run.strftime("%Y-%m-%dT%H"), hour=hour)
        offset = 0
        for _ in range(2):
            head = get(url, offset, offset + 15, timeout=60)
            offset += struct.unpack(">Q", head[8:16])[0]
        return get(url, offset, "")

    @staticmethod
    def _decode(data):
        """(east, north, gust), rows from the south as the others' (ecCodes one at a time)."""
        fields, at = {}, 0
        while at + 16 <= len(data) and data[at:at + 4] == b"GRIB":
            length = struct.unpack(">Q", data[at + 8:at + 16])[0]
            handle = eccodes.codes_new_from_message(data[at:at + length])
            try:
                fields[eccodes.codes_get(handle, "shortName")] = _values(handle)
            finally:
                eccodes.codes_release(handle)
            at += length
        if "10u" not in fields or "10v" not in fields:
            raise RuntimeError(f"fields {sorted(fields)}")
        gust = np.hypot(fields["max_10efg"], fields["max_10nfg"]) if {"max_10efg", "max_10nfg"} <= fields.keys()             else np.hypot(fields["10u"], fields["10v"])
        return fields["10u"], fields["10v"], gust

    def winds(self, times):
        """{unix time: (east, north, gust)} for the asked times the run has (its hours 1-LAST):
        four downloads at a time, decoded one after another."""
        start = int(self.run.timestamp())
        wanted = {round((t - start) / 3600): t for t in times if 3600 <= t - start <= LAST * 3600}

        def fetch(hour):
            try:
                return self._bytes(hour)
            except Exception as error:
                print(f"AROME hour {hour}: {error}", flush=True)
                return None

        out = {}
        with ThreadPoolExecutor(4) as pool:
            for hour, data in zip(wanted, pool.map(fetch, wanted)):
                if data is None:
                    continue
                try:
                    out[wanted[hour]] = self._decode(data)
                except Exception as error:
                    print(f"AROME hour {hour}: {error}", flush=True)
        return out

    def _package(self, package, hour, first=None):
        """An hour's file of a package (its first `first` messages only, by range)."""
        url = BASE + FILE.replace("/SP1/", f"/{package}/").replace("__SP1__", f"__{package}__").format(
            run=self.run.strftime("%Y-%m-%dT%H"), hour=hour)
        if first is None:
            return get(url)
        end = 0
        for _ in range(first):
            head = get(url, end, end + 15, timeout=60)
            end += struct.unpack(">Q", head[8:16])[0]
        return get(url, 0, end - 1)

    @staticmethod
    def _split(data):
        fields, at = {}, 0
        while at + 16 <= len(data) and data[at:at + 4] == b"GRIB":
            length = struct.unpack(">Q", data[at + 8:at + 16])[0]
            handle = eccodes.codes_new_from_message(data[at:at + length])
            try:
                name = eccodes.codes_get(handle, "shortName")
                if name not in fields:
                    fields[name] = _values(handle)
            finally:
                eccodes.codes_release(handle)
            at += length
        return fields

    def weather(self, times):
        """{unix time: {air, rh, wind, rain (mm/h), cloud}} for the asked times in the run: the
        rain from the sums at this frame and the one before (an hour or three)."""
        start = int(self.run.timestamp())
        hours = sorted({round((t - start) / 3600) for t in times if 3600 <= t - start <= LAST * 3600})
        if not hours:
            return {}
        previous = {h: (hours[i - 1] if i > 0 else h - 1) for i, h in enumerate(hours)}
        sp2_hours = sorted(set(hours) | set(previous.values()))

        def one(job):
            package, hour, first = job
            try:
                return job, self._package(package, hour, first)
            except Exception as error:
                print(f"AROME {package} {hour}: {error}", flush=True)
                return job, None

        jobs = [("SP1", h, 4) for h in hours] + [("SP2", h, None) for h in sp2_hours]
        with ThreadPoolExecutor(4) as pool:
            raw = dict(pool.map(one, jobs))
        sp1 = {h: self._split(raw[("SP1", h, 4)]) for h in hours if raw.get(("SP1", h, 4))}
        sp2 = {h: self._split(raw[("SP2", h, None)]) for h in sp2_hours if raw.get(("SP2", h, None))}

        def total(fields):
            return sum(fields[name] for name in ("tirf", "tsnowp", "tgrp") if name in fields)

        out = {}
        for h in hours:
            a, now, before = sp1.get(h, {}), sp2.get(h, {}), sp2.get(previous[h], {})
            if not {"2t", "2r", "10u", "10v"} <= a.keys() or "tirf" not in now or "tirf" not in before:
                continue
            clouds = [now[name] for name in ("lcc", "mcc", "hcc") if name in now]
            cloud = np.maximum.reduce(clouds) if clouds else np.zeros_like(a["2t"])
            cloud = cloud / 100 if np.nanmax(cloud) > 1.5 else cloud
            span = max(h - previous[h], 1)
            out[start + h * 3600] = {"air": a["2t"] - 273.15, "rh": np.clip(a["2r"] / 100, 0, 1),
                                     "wind": np.hypot(a["10u"], a["10v"]),
                                     "rain": np.maximum(total(now) - total(before), 0) / span, "cloud": np.clip(cloud, 0, 1)}
        return out

    def place(self, lon, lat):
        """Fractional column and row (rows from the south), the weight by the distance to the
        area's edge (the model's own, slanted, once a field is read), as the other models."""
        col = (lon - LON0) / STEP
        row = (lat - (LAT_TOP - (NJ - 1) * STEP)) / STEP
        inside = (col >= 0) & (row >= 0) & (col <= NI - 1) & (row <= NJ - 1)
        km = np.minimum.reduce([(lat - SOUTH) * 111, (NORTH - lat) * 111,
                                (lon - WEST) * 111 * np.cos(np.radians(lat)), (EAST - lon) * 111 * np.cos(np.radians(lat))])
        if _edge_km is not None:
            j = np.clip((row / COARSE).astype(int), 0, _edge_km.shape[0] - 1)
            i = np.clip((col / COARSE).astype(int), 0, _edge_km.shape[1] - 1)
            km = np.minimum(km, _edge_km[j, i])
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
