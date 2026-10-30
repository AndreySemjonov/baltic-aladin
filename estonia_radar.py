"""Estonia's two radars (Harku, Sürgavere) onto OPERA's 1 km grid for the radar Worker, which takes the stronger of
OPERA and these where they reach (station-history radar.js; the owner, 31.10.2026: "Build it now"). OPERA's composite
lost Estonia on 30.10 between 16:00 and 19:55 UTC while the radars and their open files went on; Latvia's radar has
been off since 24.09.2026, so Riga's rain then came only from the Lithuanian radars far off, then from none.

Reads Keskkonnaagentuur's open data (KAIA, https://avaandmed.keskkonnaportaal.ee, no key): every 5 minutes the two
radars' composite CAPPI (comp.<time>.CAP.<n>.h5, ODIM HDF5, Mercator ~800 m, 720 x 720). Each new one is put on
OPERA's LAEA grid (nearest pixel) for the box it covers and uploaded as radar-ee/YYYY/MM/DD/HHMM.bin (HHMM its 5-minute
step): zlib of a header (4 little-endian uint32: first column, first row, width, height on OPERA's grid) and one byte a
pixel: 0 outside the radars, 1 seen and dry, else round(dBZ x 2) + 64 (dBZ = (byte - 64) / 2).

    python estonia_radar.py [--minutes 30] [--local DIR]
"""
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://avaandmed.keskkonnaportaal.ee/api/lists/active/items"
UPLOAD = "https://data.meteogust.com/tiles/upload/radar-ee/"
OURS = "https://files.meteogust.com/radar-ee/"
AGENT = {"User-Agent": "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"}
# OPERA's DBZH grid (opera_tiff.py GRID): LAEA, 1 km, the top-left corner of pixel (0, 0).
OPERA = "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 +units=m +ellps=WGS84"
X0, Y0, PIXEL, WIDTH, HEIGHT = -500.0002714332659, 499.9999123872258, 1000.0, 3800, 4400


def request(url, body=None, method="GET", headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers={**AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as reply:
        return reply.read()


def listed(minutes):
    """The composite CAPPI files of the last `minutes`: [(id, file name)], oldest first."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = {"filter": {"and": {"children": [{"greaterThan": {"field": "Timestamp", "value": since}},
                                             {"contains": {"field": "RMFileName", "value": "comp."}},
                                             {"contains": {"field": "RMFileName", "value": ".CAP."}}]}},
             "pageSize": 100, "fields": ["RMFileName"]}
    found = json.loads(request(API + "/query", json.dumps(query).encode(), "POST", {"Content-Type": "application/json"}))
    return sorted((d["id"], d["metadata"]["RMFileName"]) for d in found.get("documents", []))


def regrid(data):
    """An ODIM composite CAPPI's bytes as (first column, first row, width, height, bytes) on OPERA's grid."""
    import io

    import h5py
    import numpy as np
    from pyproj import Transformer

    with h5py.File(io.BytesIO(data), "r") as f:
        def attrs(path):
            return {k: (v.decode() if isinstance(v, bytes) else v) for k, v in f[path].attrs.items()} if path in f else {}
        where = attrs("where")
        what = {**attrs("dataset1/what"), **attrs("dataset1/data1/what")}
        raw = f["dataset1/data1/data"][()]
    proj = where["projdef"]
    to_opera = Transformer.from_crs("EPSG:4326", OPERA, always_xy=True)
    # The box on OPERA's grid round its four corners.
    xs, ys = to_opera.transform([where[f"{c}_lon"] for c in ("LL", "LR", "UL", "UR")],
                                [where[f"{c}_lat"] for c in ("LL", "LR", "UL", "UR")])
    c0, c1 = max(int((min(xs) - X0) // PIXEL), 0), min(int((max(xs) - X0) // PIXEL) + 1, WIDTH)
    r0, r1 = max(int((Y0 - max(ys)) // PIXEL), 0), min(int((Y0 - min(ys)) // PIXEL) + 1, HEIGHT)
    w, h = c1 - c0, r1 - r0
    # Each OPERA pixel's middle in the radars' projection, then their nearest pixel (row 0 the north edge).
    cx, cy = np.meshgrid(X0 + (np.arange(c0, c1) + 0.5) * PIXEL, Y0 - (np.arange(r0, r1) + 0.5) * PIXEL)
    px, py = Transformer.from_crs(OPERA, proj, always_xy=True).transform(cx, cy)
    to_theirs = Transformer.from_crs("EPSG:4326", proj, always_xy=True)
    lx, ly = to_theirs.transform(where["LL_lon"], where["LL_lat"])
    ux, uy = to_theirs.transform(where["UR_lon"], where["UR_lat"])
    ny, nx = raw.shape
    col = np.floor((px - lx) / (ux - lx) * nx).astype(int)
    row = np.floor((uy - py) / (uy - ly) * ny).astype(int)
    inside = (col >= 0) & (col < nx) & (row >= 0) & (row < ny)
    v = raw[np.clip(row, 0, ny - 1), np.clip(col, 0, nx - 1)].astype(np.float32)
    out = np.zeros((h, w), dtype=np.uint8)
    seen = inside & (v != what["nodata"])
    out[seen] = 1
    echo = seen & (v != what["undetect"])
    dbz = v * what["gain"] + what["offset"]
    out[echo] = np.clip(np.rint(dbz[echo] * 2) + 64, 2, 255).astype(np.uint8)
    return c0, r0, w, h, out.tobytes()


def main(args):
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    minutes = int(args[args.index("--minutes") + 1]) if "--minutes" in args else 30
    token = os.environ.get("INGEST_TOKEN")
    if not local and not token:
        raise SystemExit("INGEST_TOKEN is not set")
    files = listed(minutes)
    made = 0
    for doc, name in files:
        stamp = datetime.strptime(name.split(".")[1][:12], "%Y%m%d%H%M")
        stamp -= timedelta(minutes=stamp.minute % 5)
        key = stamp.strftime("%Y/%m/%d/%H%M") + ".bin"
        if local and (local / "radar-ee" / key).exists():
            continue
        if not local:
            try:
                request(OURS + key, method="HEAD")
                continue  # made before
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
        started = time.time()
        c0, r0, w, h, pixels = regrid(request(f"{API}/{doc}/files/1"))
        body = zlib.compress(struct.pack("<4I", c0, r0, w, h) + pixels, 6)
        if local:
            path = local / "radar-ee" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        else:
            request(UPLOAD + key, body, "PUT", {"Authorization": "Bearer " + token, "Content-Type": "application/octet-stream"})
        made += 1
        print(f"{key}: {w} x {h} from column {c0}, row {r0}, {len(body) // 1024} KB in {time.time() - started:.1f} s", flush=True)
    print(f"made {made} of {len(files)} composites in the last {minutes} min", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
