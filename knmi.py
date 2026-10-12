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

    def winds(self, times):
        """{unix time: (east, north, gust)} (rows from the south) for the asked times in the run."""
        import json
        url = json.loads(_get(f"{API}/{self.name}/url", self.key))["temporaryDownloadUrl"]
        start = int(self.run.timestamp())
        wanted = {round((t - start) / 3600): t for t in times if 0 <= t - start <= 60 * 3600}
        out = {}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "run.tar")
            with urllib.request.urlopen(url, timeout=900) as reply, open(path, "wb") as f:
                while chunk := reply.read(1 << 22):
                    f.write(chunk)
            with tarfile.open(path) as tar:
                for member in tar.getmembers():
                    m = re.search(r"_(\d{3})00_GB$", member.name)
                    if not m or int(m.group(1)) not in wanted:
                        continue
                    grib = os.path.join(folder, "hour.grb")
                    with open(grib, "wb") as g:
                        g.write(tar.extractfile(member).read())
                    fields = self._fields(grib)
                    if {"u", "v"} <= fields.keys():
                        gust = np.hypot(fields["gu"], fields["gv"]) if {"gu", "gv"} <= fields.keys() else np.hypot(fields["u"], fields["v"])
                        out[wanted[int(m.group(1))]] = (fields["u"], fields["v"], gust)
        return out

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
