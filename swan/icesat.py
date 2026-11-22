"""ICESat-2's near-shore depths over a SWAN area (NASA's ATL24, "Along Track Coastal and Nearshore Bathymetry": the laser's
photons NASA classed as seabed, worldwide since 2018), to check and anchor survey.py's satellite depths (the owner,
22.11.2026, Puunsi's stony shoal read deep: "I would go with B and C", then "put secrets").

  python3 swan/icesat.py AREA [--out DIR]   ->  DIR/AREA-icesat.json: {"area", "points": [[lat, lon, depth m below the
                                               sea surface the laser saw, "YYYY-MM-DD", beam], ...], "granules", "files"}

Reads only the area's part of each file (earthaccess opens them over HTTPS; h5py reads the chunks asked for). The login
from EARTHDATA_USERNAME / EARTHDATA_PASSWORD (the owner's NASA Earthdata account, baltic-aladin secrets). Lazy imports:
only this job installs earthaccess and h5py.
"""
import json
import math
import sys
import time
from pathlib import Path

BEAMS = ("gt1l", "gt1r", "gt2l", "gt2r", "gt3l", "gt3r")
BATHY = 40  # class_ph: seabed (41 the sea surface)


def area_bbox(name):
    """The area's (west, south, east, north) from the store's index."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tables as T

    a = next((x for x in (T._json(T.STORE + "index.json") or {}).get("areas", []) if x["area"] == name), None)
    if a is None:
        raise SystemExit(f"{name}: not in the index")
    x, y, w, h = a["box"]
    lat = lambda my: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * my))))  # noqa: E731
    return x * 360 - 180, lat(y + h), (x + w) * 360 - 180, lat(y)


def points(name, out):
    import earthaccess
    import h5py
    import numpy as np

    W, S, E, N = area_bbox(name)
    earthaccess.login(strategy="environment")
    found = earthaccess.search_data(short_name="ATL24", bounding_box=(W, S, E, N))
    # (The newest version of each pass: the catalogue lists every version's file.)
    newest = {}
    for g in found:
        title = g["umm"]["GranuleUR"]
        # (ATL24_<time>_<track,cycle,segment>_<ATL03 release>_<ATL03 version>_<ATL24 version>_<revision>)
        part = title.split("_")
        key, ver = part[1] + part[2], part[5]
        if key not in newest or ver > newest[key][0]:
            newest[key] = (ver, g)
    grans = [g for _, g in newest.values()]
    print(name, f"{len(found)} files listed, {len(grans)} passes", flush=True)
    pts, used, shown = [], 0, False
    for f, g in zip(earthaccess.open(grans), grans):
        title = g["umm"]["GranuleUR"]
        day = f"{title[6:10]}-{title[10:12]}-{title[12:14]}"
        try:
            with h5py.File(f, "r") as h:
                if not shown:
                    print("beam fields:", sorted(h[next(b for b in BEAMS if b in h)].keys()), flush=True)
                    shown = True
                n0 = len(pts)
                for b in BEAMS:
                    if b not in h:
                        continue
                    lat, lon = h[b]["lat_ph"][:], h[b]["lon_ph"][:]
                    k = np.nonzero((lat >= S) & (lat <= N) & (lon >= W) & (lon <= E))[0]
                    if not len(k):
                        continue
                    i0, i1 = k.min(), k.max() + 1
                    cls = h[b]["class_ph"][i0:i1]
                    oh, sh = h[b]["ortho_h"][i0:i1], h[b]["surface_h"][i0:i1]
                    la, lo = lat[i0:i1], lon[i0:i1]
                    keep = (cls == BATHY) & (la >= S) & (la <= N) & (lo >= W) & (lo <= E)
                    for y, x, d in zip(la[keep], lo[keep], (sh - oh)[keep]):
                        pts.append([round(float(y), 6), round(float(x), 6), round(float(d), 2), day, b])
                used += len(pts) > n0
        except Exception as error:  # (a file that won't open: told, the rest go on)
            print(title, "skipped:", error, flush=True)
    res = {"area": name, "bbox": [W, S, E, N], "granules": len(grans), "files": used, "points": pts,
           "made": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"{name}-icesat.json").write_text(json.dumps(res))
    print(name, f"{len(pts)} seabed points from {used} passes", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    points(args[0], args[args.index("--out") + 1] if "--out" in args else "icesat")
