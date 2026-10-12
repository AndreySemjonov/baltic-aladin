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
                fields[eccodes.codes_get(handle, "shortName")] = eccodes.codes_get_values(handle).astype(np.float32).reshape(NJ, NI)[::-1]
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

    def place(self, lon, lat):
        """Fractional column and row (rows from the south), the weight by the distance to the
        area's edge, as the other models."""
        col = (lon - LON0) / STEP
        row = (lat - (LAT_TOP - (NJ - 1) * STEP)) / STEP
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
