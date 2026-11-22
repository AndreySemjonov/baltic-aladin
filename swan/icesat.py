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


def _span(ds, lo, hi):
    """A track's photons with lo <= lat <= hi as indices [i0, i1): lat runs one way along a track here (the orbit turns
    only at ~92 N/S), so a binary search reads ~2 x 25 single values, not the whole track (the first run read every
    beam's whole lat and lon: ~70 passes took over 40 min; the owner, 22.11.2026: "make it faster for next areas")."""
    n = ds.shape[0]
    if n == 0:
        return 0, 0
    up = float(ds[n - 1]) >= float(ds[0])

    def first(pred):
        L, R = 0, n
        while L < R:
            m = (L + R) // 2
            if pred(float(ds[m])):
                R = m
            else:
                L = m + 1
        return L

    if up:
        return first(lambda v: v >= lo), first(lambda v: v > hi)
    return first(lambda v: v <= hi), first(lambda v: v < lo)


_LOGGED = []


def _pass(job):
    """One pass's seabed points in the box: [[lat, lon, depth, day, beam], ...]; its time printed."""
    import earthaccess
    import h5py

    url, title, (W, S, E, N) = job
    if not _LOGGED:
        earthaccess.login(strategy="environment")
        _LOGGED.append(True)
    day, t0, pts, note = f"{title[6:10]}-{title[10:12]}-{title[12:14]}", time.time(), [], ""
    try:
        # (Small blocks, kept: the default 5 MB read-ahead fetched megabytes for each single value the search reads.)
        fs = earthaccess.get_fsspec_https_session()
        with fs.open(url, block_size=256 * 1024, cache_type="blockcache") as f, h5py.File(f, "r") as h:
            for b in BEAMS:
                if b not in h or "lat_ph" not in h[b]:
                    continue
                i0, i1 = _span(h[b]["lat_ph"], S, N)
                if i1 <= i0:
                    continue
                g = h[b]
                la, lo, cls = g["lat_ph"][i0:i1], g["lon_ph"][i0:i1], g["class_ph"][i0:i1]
                d = g["surface_h"][i0:i1] - g["ortho_h"][i0:i1]
                keep = (cls == BATHY) & (lo >= W) & (lo <= E)
                pts += [[round(float(y), 6), round(float(x), 6), round(float(z), 2), day, b]
                        for y, x, z in zip(la[keep], lo[keep], d[keep])]
    except Exception as error:  # (a file that won't open: told, the rest go on)
        note = f", skipped: {error}"
    print(f"{title}: {len(pts)} points in {time.time() - t0:.0f} s{note}", flush=True)
    return pts


def points(name, out):
    import earthaccess
    from concurrent.futures import ProcessPoolExecutor

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
    jobs = []
    for g in grans:
        links = [u for u in g.data_links(access="external") if u.endswith(".h5")]
        if links:
            jobs.append((links[0], g["umm"]["GranuleUR"], (W, S, E, N)))
    # (Four passes at a time, one a core: h5py holds one lock in a process.)
    t0, pts, used = time.time(), [], 0
    with ProcessPoolExecutor(4) as ex:
        for p in ex.map(_pass, jobs):
            pts += p
            used += bool(p)
    print(name, f"{len(jobs)} passes read in {time.time() - t0:.0f} s", flush=True)
    res = {"area": name, "bbox": [W, S, E, N], "granules": len(grans), "files": used, "points": pts,
           "made": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"{name}-icesat.json").write_text(json.dumps(res))
    print(name, f"{len(pts)} seabed points from {used} passes", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    points(args[0], args[args.index("--out") + 1] if "--out" in args else "icesat")
