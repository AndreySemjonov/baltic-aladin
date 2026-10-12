"""KNMI's HARMONIE-AROME Netherlands ("HARM-NL", 2 km; open data, CC BY 4.0, with a free key:
https://dataplatform.knmi.nl/) for the regional tiles (regional_tiles.py): the Netherlands,
Belgium, the French Channel coast, the southern North Sea and north-west Germany. Each hourly run
is one tar (about 860 MB) of 61 GRIB-1 files, one an hour ahead (HA43_N20_<run>_<hhh>00_GB); in
each the 10 m wind (parameters 33 and 34 at 10 m) and the hour's gust as its parts (162 and 163),
on a regular grid of 390 x 390 from 49N, 0E. The key from KNMI_API_KEY (a GitHub secret).
"""
import os
import re
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone

import eccodes
import numpy as np

API = "https://api.dataplatform.knmi.nl/open-data/v1/datasets/harmonie_arome_cy43_p1/versions/1.0/files"
NI, NJ = 390, 390
LAT0, LAT1, LON0, LON1 = 49.0, 56.002, 0.0, 11.281
FADE_KM = 50


def _get(url, key, timeout=120):
    request = urllib.request.Request(url, headers={"Authorization": key})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply.read()


class Model:
    """The newest run: its winds for the hours asked."""

    def __init__(self):
        import json
        self.key = os.environ.get("KNMI_API_KEY", "")
        if not self.key:
            raise RuntimeError("KNMI_API_KEY is not set")
        listing = json.loads(_get(API + "?maxKeys=5&orderBy=created&sorting=desc", self.key))
        self.name = listing["files"][0]["filename"]
        stamp = re.search(r"(\d{10})\.tar$", self.name).group(1)
        self.run = datetime.strptime(stamp, "%Y%m%d%H").replace(tzinfo=timezone.utc)

    def _hour_files(self, hours):
        """{hour: that hour's GRIB file} for the hours asked, read from the run's tar as it comes in
        (not saved first; the tar's hours 0-60 come in order, about 14 MB each, so the download
        stops after the last one asked: the regional tiles' 35 hours, about 60 % of it)."""
        import json
        url = json.loads(_get(f"{API}/{self.name}/url", self.key))["temporaryDownloadUrl"]
        out = {}
        if not hours:
            return out
        with urllib.request.urlopen(url, timeout=900) as reply, tarfile.open(fileobj=reply, mode="r|") as tar:
            for member in tar:
                m = re.search(r"_(\d{3})00_GB$", member.name)
                if m and int(m.group(1)) in hours:
                    out[int(m.group(1))] = tar.extractfile(member).read()
                    if hours <= out.keys():
                        break
        return out

    def winds(self, times):
        """{unix time: (east, north, gust)} (rows from the south) for the asked times in the run."""
        start = int(self.run.timestamp())
        wanted = {round((t - start) / 3600): t for t in times if 0 <= t - start <= 60 * 3600}
        out = {}
        files = self._hour_files(set(wanted))
        with tempfile.TemporaryDirectory() as folder:
            for hour, data in files.items():
                grib = os.path.join(folder, "hour.grb")
                with open(grib, "wb") as g:
                    g.write(data)
                fields = self._fields(grib)
                if {"u", "v"} <= fields.keys():
                    gust = np.hypot(fields["gu"], fields["gv"]) if {"gu", "gv"} <= fields.keys() else np.hypot(fields["u"], fields["v"])
                    out[wanted[hour]] = (fields["u"], fields["v"], gust)
        return out

    def weather(self, times):
        """{unix time: {air, rh, wind, rain (mm/h), cloud}} for the asked times in the run (its
        precipitation summed from the start: each hour's from the hour before)."""
        start = int(self.run.timestamp())
        hours = {round((t - start) / 3600) for t in times if 3600 <= t - start <= 60 * 3600}
        need = hours | {h - 1 for h in hours}
        names = {(11, 2): "air", (52, 2): "rh", (33, 10): "u", (34, 10): "v", (61, 0): "total", (71, 0): "cloud"}
        per_hour = {}
        files = self._hour_files(need)
        with tempfile.TemporaryDirectory() as folder:
            for hour, data in files.items():
                grib = os.path.join(folder, "hour.grb")
                with open(grib, "wb") as g:
                    g.write(data)
                per_hour[hour] = self._pick(grib, names)
        out = {}
        for h in sorted(hours):
            now, before = per_hour.get(h, {}), per_hour.get(h - 1, {})
            if not {"air", "rh", "u", "v", "cloud", "total"} <= now.keys() or "total" not in before:
                continue
            air = now["air"] - 273.15 if np.nanmean(now["air"]) > 150 else now["air"]
            rh = now["rh"] / 100 if np.nanmax(now["rh"]) > 1.5 else now["rh"]
            cloud = now["cloud"] / 100 if np.nanmax(now["cloud"]) > 1.5 else now["cloud"]
            out[start + h * 3600] = {"air": air, "rh": np.clip(rh, 0, 1), "wind": np.hypot(now["u"], now["v"]),
                                     "rain": np.maximum(now["total"] - before["total"], 0), "cloud": np.clip(cloud, 0, 1)}
        return out

    @staticmethod
    def _pick(path, names):
        """The (parameter, level) fields of one hour's file, by name (the first of each)."""
        found = {}
        with open(path, "rb") as f:
            while True:
                handle = eccodes.codes_grib_new_from_file(f)
                if handle is None:
                    break
                try:
                    key = (eccodes.codes_get(handle, "indicatorOfParameter"), eccodes.codes_get(handle, "level"))
                    if key in names and names[key] not in found:
                        found[names[key]] = eccodes.codes_get_values(handle).astype(np.float32).reshape(NJ, NI)
                finally:
                    eccodes.codes_release(handle)
        return found

    @staticmethod
    def _fields(path):
        """The 10 m wind and gust parts of one hour's file."""
        names = {33: "u", 34: "v", 162: "gu", 163: "gv"}
        found = {}
        with open(path, "rb") as f:
            while True:
                handle = eccodes.codes_grib_new_from_file(f)
                if handle is None:
                    break
                try:
                    param, level = eccodes.codes_get(handle, "indicatorOfParameter"), eccodes.codes_get(handle, "level")
                    if level == 10 and param in names and names[param] not in found:
                        found[names[param]] = eccodes.codes_get_values(handle).astype(np.float32).reshape(NJ, NI)
                finally:
                    eccodes.codes_release(handle)
        return found

    def place(self, lon, lat):
        """Fractional column and row on the grid, how much HARM-NL counts there (by the distance
        to its area's edge), and whether the point lies on it."""
        col = (lon - LON0) / (LON1 - LON0) * (NI - 1)
        row = (lat - LAT0) / (LAT1 - LAT0) * (NJ - 1)
        inside = (col >= 0) & (row >= 0) & (col <= NI - 1) & (row <= NJ - 1)
        km = np.minimum.reduce([(lat - LAT0) * 111, (LAT1 - lat) * 111,
                                (lon - LON0) * 111 * np.cos(np.radians(lat)), (LON1 - lon) * 111 * np.cos(np.radians(lat))])
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
