"""MET Norway's Nordic radar composite over Norway and Finland onto OPERA's 1 km grid for the radar Worker, which takes
it there instead of OPERA's (station-history radar.js; the owner, 02.11.2026, "Norway+Finland only"). Against 4,408
METARs of 01-02.11 their composite (nordic_radar.py's lab) scored CSI 0.38 in Norway against OPERA's 0.20 and 0.64 in
Finland against 0.59, fewer false echoes (Bålsta's spikes, the sun's rays); over Sweden about the same, over the Baltics
far worse (Swedish and Finnish beams 200+ km out, above the low rain), so only where a Norwegian or Finnish radar is the
nearest and within 150 km.

Reads thredds.met.no's day file over OPeNDAP (CC BY 4.0, no key): every 5 minutes, ~6 minutes after its time. Each
step's reflectivity on OPERA's LAEA grid (nearest pixel), their clutter dry, uploaded as radar-no/YYYY/MM/DD/HHMM.bin
in estonia_radar.py's form: zlib of 4 little-endian uint32 (first column, first row, width, height on OPERA's grid),
then a byte a pixel: 0 outside, 1 seen and dry, else round(dBZ x 2) + 64.

    python metno_radar.py [--minutes 30] [--local DIR]
"""
import math
import os
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

THREDDS = "https://thredds.met.no/thredds/dodsC/remotesensing/reflectivity-nordic/"
NAME = "yrwms-nordic.mos.pcappi-0-dbz.noclass-clfilter-novpr-clcorr-block.nordiclcc-1000."
UPLOAD = "https://data.meteogust.com/tiles/upload/radar-no/"
OURS = "https://files.meteogust.com/radar-no/"
AGENT = {"User-Agent": "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"}
OPERA = "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 +units=m +ellps=WGS84"
X0, Y0, PIXEL, WIDTH, HEIGHT = -500.0002714332659, 499.9999123872258, 1000.0, 3800, 4400
REACH_KM = 150
# OPERA's radars round Norway and Finland (radar.js RADAR_SITES): theirs, then their neighbours, so a pixel nearer a
# Swedish, Estonian or Danish radar keeps OPERA's.
OURS_SITES = [  # Norway, then Finland
    (59.854, 5.09), (67.53, 12.099), (69.241, 16.003), (70.605, 22.443), (62.187, 5.127), (59.627, 10.564), (58.36, 7.165),
    (63.691, 10.204), (70.511, 29.018), (65.22, 11.993), (61.232, 10.527), (69.219, 23.44),
    (60.904, 27.108), (62.863, 27.381), (60.129, 21.643), (64.775, 26.319), (67.139, 26.897), (63.105, 23.821),
    (61.907, 29.798), (62.304, 25.44), (63.838, 29.449), (60.556, 24.496), (61.811, 22.502), (68.434, 27.444)]
OTHER_SITES = [  # Sweden, Estonia, Denmark
    (67.709, 20.618), (65.431, 21.865), (63.295, 14.759), (63.639, 18.402), (61.577, 16.714), (60.723, 14.878),
    (59.611, 17.583), (57.303, 18.4), (58.106, 15.936), (58.256, 12.826), (56.367, 12.852), (56.296, 15.61),
    (59.397, 24.602), (58.482, 25.519), (55.326, 12.449), (55.173, 8.552), (57.489, 10.136), (55.113, 14.887), (55.812, 10.585)]


def request(url, body=None, method="GET", headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method, headers={**AGENT, **(headers or {})}),
                                timeout=60) as r:
        return r.read()


def area():
    """OPERA's pixels taken (first column, first row, width, height, mask) and their latitudes and longitudes."""
    import numpy as np
    from pyproj import Transformer

    to_opera = Transformer.from_crs("EPSG:4326", OPERA, always_xy=True)
    lats, lons = zip(*OURS_SITES)
    xs, ys = to_opera.transform(lons, lats)
    c0 = max(int((min(xs) - REACH_KM * 1000 - X0) // PIXEL), 0)
    c1 = min(int((max(xs) + REACH_KM * 1000 - X0) // PIXEL) + 1, WIDTH)
    r0 = max(int((Y0 - max(ys) - REACH_KM * 1000) // PIXEL), 0)
    r1 = min(int((Y0 - min(ys) + REACH_KM * 1000) // PIXEL) + 1, HEIGHT)
    cx, cy = np.meshgrid(X0 + (np.arange(c0, c1) + 0.5) * PIXEL, Y0 - (np.arange(r0, r1) + 0.5) * PIXEL)
    lon, lat = Transformer.from_crs(OPERA, "EPSG:4326", always_xy=True).transform(cx, cy)

    def km(site):  # equirectangular, good to a few km at 150 km
        return np.hypot((lat - site[0]) * 111.2, (lon - site[1]) * 111.2 * math.cos(math.radians(site[0])))

    ours = np.min([km(s) for s in OURS_SITES], axis=0)
    other = np.min([km(s) for s in OTHER_SITES], axis=0)
    mask = (ours <= REACH_KM) & (ours < other)
    return c0, r0, c1 - c0, r1 - r0, mask, lat, lon


def main(args):
    import numpy as np
    from netCDF4 import Dataset
    from pyproj import Transformer

    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    minutes = int(args[args.index("--minutes") + 1]) if "--minutes" in args else 30
    token = os.environ.get("INGEST_TOKEN")
    if not local and not token:
        raise SystemExit("INGEST_TOKEN is not set")
    now = datetime.now(timezone.utc)
    made, where = 0, None
    for day in sorted({(now - timedelta(minutes=m)).date() for m in (0, minutes)}):
        try:
            data = Dataset(f"{THREDDS}{day:%Y/%m}/{NAME}{day:%Y%m%d}.nc")
        except OSError as error:  # the new day's file appears a little after 00 UTC
            print(f"{day}: not there yet ({error.__class__.__name__})", flush=True)
            continue
        for k, stamp in enumerate(data["time"][:]):  # seconds since 1970
            when = datetime.fromtimestamp(int(stamp), timezone.utc)
            if when < now - timedelta(minutes=minutes):
                continue
            key = when.strftime("%Y/%m/%d/%H%M") + ".bin"
            if local and (local / "radar-no" / key).exists():
                continue
            if not local:
                try:
                    request(OURS + key, method="HEAD")
                    continue  # made before
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
            started = time.time()
            if where is None:
                xc, yc = data["Xc"][:], data["Yc"][:]
                # Their grid onto ours once, kept beside the system's temporary files (~5 s of the step's ~12).
                saved = Path(tempfile.gettempdir()) / f"metno_radar_{len(xc)}x{len(yc)}_{REACH_KM}.npz"
                try:
                    z = np.load(saved)
                    where = (z["i"], z["j"], z["mask"], *z["box"])
                except (OSError, KeyError, ValueError):
                    c0, r0, w, h, mask, lat, lon = area()
                    gx, gy = Transformer.from_crs("EPSG:4326", data["projection_lambert"].proj4, always_xy=True).transform(lon, lat)
                    i = np.round((gy - yc[0]) / (yc[1] - yc[0])).astype(int)
                    j = np.round((gx - xc[0]) / (xc[1] - xc[0])).astype(int)
                    mask &= (i >= 0) & (i < len(yc)) & (j >= 0) & (j < len(xc))
                    where = (np.clip(i, 0, len(yc) - 1).astype(np.int32), np.clip(j, 0, len(xc) - 1).astype(np.int32), mask, c0, r0, w, h)
                    np.savez(saved, i=where[0], j=where[1], mask=mask, box=np.array([c0, r0, w, h]))
            i, j, mask, c0, r0, w, h = where
            c0, r0, w, h = int(c0), int(r0), int(w), int(h)
            field = lambda name, fill=0: np.ma.filled(data[name][k, :, :], fill)[i, j]
            dbz = field("equivalent_reflectivity_factor", np.nan)
            seen = mask & (field("is_nodata", 1) == 0)
            clutter = (field("is_groundclutter") > 0) | (field("is_seaclutter") > 0) | (field("is_otherclutter") > 0)
            out = np.zeros((h, w), dtype=np.uint8)
            out[seen] = 1
            echo = seen & ~clutter & np.isfinite(dbz)
            out[echo] = np.clip(np.rint(dbz[echo] * 2) + 64, 1, 255).astype(np.uint8)
            body = zlib.compress(struct.pack("<4I", c0, r0, w, h) + out.tobytes(), 6)
            if local:
                path = local / "radar-no" / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            else:
                request(UPLOAD + key, body, "PUT", {"Authorization": "Bearer " + token, "Content-Type": "application/octet-stream"})
            made += 1
            print(f"{key}: {w} x {h} from column {c0}, row {r0}, {int(seen.sum())} seen, {int((out > 1).sum())} echo, "
                  f"{len(body) // 1024} KB in {time.time() - started:.1f} s", flush=True)
    print(f"made {made} in the last {minutes} min", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
