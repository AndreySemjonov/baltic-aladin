"""A spot's shallows from the satellite: the automatic survey's depth step (the owner, 20.11.2026, Svetciems's satellite
picture: "we should include this somehow in automatic process of detailed spot when some spot comes as official ... in
Svetciems we have sandbars too what make flat water on low water level. But after all live we do not have it", then
"we will make svetciems later as pilot of automatic spot process"; WindguruStationWatch docs/backlog/design.md "SWAN spots").

Sentinel-2 L2A (earth-search on AWS, no account; its values already have the offset in), a 4 x 4 km window round the
area's spot, 2023 on (bars move), clear scenes outside the ice months. Calm scenes (no foam or glint near the shore, a
dark open sea) give the water's colour, the median of their ln(green - the deep water's green): brighter is shallower.
Two anchors set its scale (depth = a + b L): the brightest water (p98, off the beach's mixed pixels) is the bars' crest,
CREST below mean sea level (0.30: the measured spots' median), and ordinary water where our depth grid (area.py, no bars) has
1.5-3 m is that deep. The crest's height measured where it can be (the owner, 21.11.2026, Berzciems: "Use the measured
value"; for spots nobody knows): the Baltic's level swings with the wind, so each scene sees the sand at another level;
with the level at each scene's moment (EMODnet Physics' hourly gauges, open: the nearest within 40 km) the brightest
sand's height is the level that splits its wet scenes from its dry ones. Else the owner's (bars.json "crest"), else CREST.
The depths kept as a picture, not contours (the owner, 20.11.2026: "this is what we need to implement in our sandbank ...
for new [spots] the same approach from the beginning"): OUT/AREA-bed.json + .bin.gz on the area's fine grid (area.py
bed_for, _bed; --apply puts it in beds/ and bars.json "bed"); river channels (narrow water) and plants (near-infrared
bright: reeds, mats) left out. A dark bottom (submerged weed, stones) reads as depth in green: where red (swallowed by
1-2 m of brown water) still comes back, the depth from red against green; a reading 1.2 m deeper than the ~110 m round
it evened to that; a wide deep band at the beach still left flagged. A river joined to the sea near the spot is
reported, not opened. Every survey writes a review sheet (AREA.png: true colour beside the depth, the numbers, the
doubts): the standard for every spot (the owner, 21.11.2026, "B"): the survey measures, a Claude review reads the sheets
(live / fixed / ask the owner), the owner only for what it can't settle.

Doubtful, so it waits for the owner (the owner, 20.11.2026: "Confident parts go live straight away"): few calm scenes, no
1.5-3 m water to anchor on, a light fall-off with depth (2K) out of what clear to brown Baltic water gives, the two
halves of the scenes disagreeing, many plants, a river. Limits: an estimate; the Gulf of Riga's brown water and dark
bottoms blur it below ~1.5-2 m; the scenes' water levels differ (the median's level is roughly mean sea level).

  (The crest from bars.json's "crest" where the owner knew it: Svetciems's bars dry at -0.3, so 0.3.)
  python3 swan/survey.py AREA [--data DIR] [--out DIR] [--crest M]   the scenes into DIR/AREA (kept), OUT/AREA.json
                                                                     (the shallows, the fit, the doubts) and AREA.png
  python3 swan/survey.py AREA --apply [--force] [--keep-bars]        that survey into beds/ and bars.json (doubtful: only
                                                                     --force; the rule's bars kept under it, --drop-bars drops them)
  python3 swan/survey.py plan [--only baltic|all|a,b]               the areas to survey (JSON; survey.yml's batch)
  python3 swan/survey.py summary DIR                                 the batch's SUMMARY.md for the review
"""
import collections
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import area as A  # noqa: E402

HERE = Path(__file__).resolve().parent
STAC = "https://earth-search.aws.element84.com/v1/search"
ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES", GDAL_HTTP_MAX_RETRY="4",
           GDAL_HTTP_RETRY_DELAY="3", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")
N = 400  # the window, 10 m pixels
SINCE = "2023-01-01"
LEVELS = (-0.25, 0.1, 0.3, 0.6, 1.0)  # (the picture's and the report's bands)
GAUGES = "https://erddap.emodnet-physics.eu/erddap/tabledap/ERD_EP_SLEV_NRT_60m.csv?"
CREST = 0.30  # (the waterline's median over the Baltic batch's 31 measured spots, 21.11.2026: 0.17-0.42 m below mean)
MIN_CALM, TWO_K, HALVES, PLANTS = 8, (0.15, 1.5), 0.25, 0.2
BANDS = ("green", "nir", "red", "blue")  # (green and near-infrared the depth's; red and blue the review's true colour)
RED = 0.6  # red against green (log) this far from the 1.5-3 m water's towards the sand's: under ~1 m whatever green says
DARK = 1.2  # a reading this much deeper than the ~110 m round it: a dark bottom (weed, stones), not a hole


def _post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    for k in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception:
            if k == 3:
                raise
            time.sleep(5)


def search(lat, lon):
    """Clear-ish (< 50 % cloud) L2A scenes over the point since SINCE, one a day and tile."""
    body = {"collections": ["sentinel-2-l2a"], "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "datetime": f"{SINCE}T00:00:00Z/{time.strftime('%Y-%m-%d')}T00:00:00Z", "limit": 200,
            "query": {"eo:cloud_cover": {"lt": 50}}}
    feats, url = [], STAC
    while url:
        j = _post(url, body)
        feats += j["features"]
        nxt = [x for x in j.get("links", []) if x["rel"] == "next"]
        url, body = (nxt[0]["href"], nxt[0].get("body", body)) if nxt else (None, body)
    byday = {}
    for f in sorted(feats, key=lambda f: f["id"]):
        p, a = f["properties"], f["assets"]
        if all(k in a for k in BANDS + ("scl",)):
            tile = f"{p.get('mgrs:utm_zone')}{p.get('mgrs:latitude_band')}{p.get('mgrs:grid_square')}"
            byday[(p["datetime"][:10], tile)] = {"date": p["datetime"][:10], "tile": tile,
                                                 **{k: a[k]["href"] for k in BANDS + ("scl",)}}
    return list(byday.values())


def fetch(name, lat, lon, data):
    """The window's green, near-infrared, red and blue (10 m) from every scene under 3 % cloud there, into data/name
    (kept: a rerun fetches only new scenes, and scenes kept with two bands again)."""
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import Window

    out = Path(data) / name
    out.mkdir(parents=True, exist_ok=True)
    items = [i for i in search(lat, lon) if not (lat > 50 and int(i["date"][5:7]) in (12, 1, 2))]  # (the Baltic's ice)
    win = None
    for tile, _ in collections.Counter(i["tile"] for i in items).most_common():
        with rasterio.Env(**ENV), rasterio.open(next(i for i in items if i["tile"] == tile)["green"]) as d:
            (x,), (y,) = transform("EPSG:4326", d.crs, [lon], [lat])
            r, c = d.index(x, y)
            r0, c0 = int(r - N // 2), int(c - N // 2)
            if r0 >= 0 and c0 >= 0 and r0 + N <= d.height and c0 + N <= d.width:
                win, T, crs = Window(c0, r0, N, N), d.transform, str(d.crs)
                break
    if win is None:
        raise SystemExit(f"{name}: no tile holds the window")
    items = [i for i in items if i["tile"] == tile]
    (out / "meta.json").write_text(json.dumps({"tile": tile, "crs": crs, "x0": T.c + win.col_off * 10,
                                               "y0": T.f - win.row_off * 10, "lat": lat, "lon": lon, "items": len(items)}))
    swin = Window(win.col_off // 2, win.row_off // 2, N // 2, N // 2)

    def one(it):
        f = out / f"{it['date']}.npz"
        if f.exists() and len(np.load(f)["b"]) == len(BANDS):
            return "have"
        try:
            with rasterio.Env(**ENV):
                with rasterio.open(it["scl"]) as d:
                    if np.isin(d.read(1, window=swin), [0, 3, 8, 9, 10, 11]).mean() > 0.03:
                        return "cloudy"
                bands = []
                for k in BANDS:
                    with rasterio.open(it[k]) as d:
                        bands.append(d.read(1, window=win))
            np.savez_compressed(f, b=np.stack(bands).astype(np.uint16))
            return "ok"
        except Exception:
            return "failed"

    with ThreadPoolExecutor(8) as ex:
        print(name, dict(collections.Counter(ex.map(one, items))), flush=True)
    return out


def _rdp(p, tol):
    """Douglas-Peucker on an open polyline (n x 2)."""
    keep, stack = np.zeros(len(p), bool), [(0, len(p) - 1)]
    keep[[0, -1]] = True
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = p[i], p[j]
        ab = b - a
        L = np.hypot(*ab)
        q = p[i + 1:j] - a
        d = np.abs(ab[0] * q[:, 1] - ab[1] * q[:, 0]) / L if L > 0 else np.hypot(q[:, 0], q[:, 1])
        k = int(np.argmax(d))
        if d[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return p[keep]


def _ring(comp, meta, toLL):
    """A component's outline (holes filled) as [[lat, lon], ...], at most 60 points."""
    from rasterio.features import shapes

    ext = max((g["coordinates"][0] for g, v in shapes(comp.astype(np.uint8), mask=comp) if v), key=len)
    xy = np.array(ext, float) * 10 * np.array([1, -1]) + np.array([meta["x0"], meta["y0"]])
    for tol in np.arange(8, 200, 4):
        p = _rdp(xy, tol)
        if len(p) <= 60:
            break
    lon, lat = toLL(p[:, 0], p[:, 1])
    ring = [[round(float(b), 6), round(float(a), 6)] for a, b in zip(lon, lat)]
    return ring if ring[0] == ring[-1] else ring + [ring[0]]


def _erddap(q):
    import urllib.parse

    url = GAUGES + urllib.parse.quote(q, safe="&=(),")
    req = urllib.request.Request(url, headers={"User-Agent": "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return [line.split(",") for line in r.read().decode().splitlines()[2:]]


def _copernicus_levels(lat, lon, days):
    """Copernicus Marine's Baltic model's sea level (sla, the map's own: copernicus.py) at the spot's nearest sea cell at
    each scene's moment (09-11 UTC): {day: m}; {} without a login (COPERNICUSMARINE_SERVICE_USERNAME/_PASSWORD)."""
    import os

    if not (os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME") and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD")):
        return {}
    try:
        import copernicusmarine

        ds = copernicusmarine.open_dataset(dataset_id="cmems_mod_bal_phy_anfc_PT1H-i", variables=["sla"],
                                           minimum_latitude=lat - 0.03, maximum_latitude=lat + 0.03,
                                           minimum_longitude=lon - 0.05, maximum_longitude=lon + 0.05, service="arco-time-series")
        v = ds["sla"].values
        cell = np.nanargmin(np.where(np.isfinite(v).all(0), 0, 1)) if np.isfinite(v).all(0).any() else None
        if cell is None:
            return {}
        s = v.reshape(len(v), -1)[:, cell]
        times = [str(x)[:13] for x in ds["time"].values]
    except Exception as e:
        print("Copernicus:", str(e)[:200], flush=True)
        return {}
    hour = dict(zip(times, s))
    out = {}
    for day in days:
        x = [hour[f"{day}T{h:02d}"] for h in (9, 10, 11) if f"{day}T{h:02d}" in hour and np.isfinite(hour[f"{day}T{h:02d}"])]
        if x:
            out[day] = float(np.mean(x))
    return out


def levels_at(lat, lon, days, reach=40000):
    """The sea's level at each scene's moment (09-11 UTC, Sentinel-2's pass over the Baltic) on the map's own zero:
    Copernicus's model, the map's level as it is (station-history /sea: copernicus.py's sla, no shift); the nearest
    EMODnet gauge within reach metres only where Copernicus has too few days (its zero then the gauge's, flagged).
    {day: level}, where from (the gauge's difference from the model reported). (21.11.2026: the Latvian gauges in
    EMODnet's set read ~0.30 m above the model, the Estonian ~0; measured on the gauges, the Latvian tops came out 0.3 m
    too high on the map; on the model's zero Svetciems's bars dry at -0.28, the owner's "around -0.3".)"""
    k = math.cos(math.radians(lat))
    q = (f"PLATFORMCODE,latitude,longitude&latitude>={lat - 0.5}&latitude<={lat + 0.5}&longitude>={lon - 0.5 / k}"
         f"&longitude<={lon + 0.5 / k}&distinct()")
    try:
        gauges = sorted((math.hypot(float(g[1]) - lat, (float(g[2]) - lon) * k) * 111195.0, g[0]) for g in _erddap(q))
    except Exception as e:
        print("gauges:", e, flush=True)
        gauges = []
    gauge, name = {}, None
    for dist, g in gauges:
        if dist > reach:
            break
        try:
            rows = _erddap(f'time,SLEV,SLEV_QC&PLATFORMCODE="{g}"&time>={SINCE}')
        except Exception:
            continue
        hour = {r[0][:13]: float(r[1]) for r in rows if len(r) > 2 and r[2] in ("1", "2") and r[1]}
        for day in days:
            v = [hour[f"{day}T{h:02d}"] for h in (9, 10, 11) if f"{day}T{h:02d}" in hour]
            if v:
                gauge[day] = sum(v) / len(v)
        if len(gauge) >= 12:
            name = f"{g} ({dist / 1000:.0f} km)"
            break
        gauge = {}
    cop = _copernicus_levels(lat, lon, days)
    both = [d for d in gauge if d in cop]
    note = f"; gauge {name} {float(np.median([gauge[d] - cop[d] for d in both])):+.2f} m from it" if len(both) >= 10 else ""
    if len(cop) >= 12:
        return cop, "Copernicus (the map's level)" + note
    return gauge, (f"{name}: the gauge's zero, not the map's" if name else None)


def waterline(G, NI, days, lv, top, L):
    """The brightest offshore sand's height (m above the map's zero, levels_at) from the scenes' wet and dry at their levels
    (levels_at): each pixel of top (a mask) sometimes dry, sometimes wet, at the level that splits them with at most a
    fifth of its scenes on the wrong side, inside the levels seen; the median, how many, and their colour's median (L):
    the fit's anchor. None under 30 pixels."""
    k = [i for i, d in enumerate(days) if d in lv]
    if len(k) < 12:
        return None, 0
    LV = np.array([lv[days[i]] for i in k])
    with np.errstate(invalid="ignore", divide="ignore"):
        wet = (G[k] - NI[k]) / (G[k] + NI[k]) > 0
    ok = np.isfinite(G[k]) & np.isfinite(NI[k])
    nw, nd = (wet & ok).sum(0), (~wet & ok).sum(0)
    lo, hi = np.sort(LV)[1], np.sort(LV)[-2]
    hs, ls = [], []
    for y, x in zip(*np.nonzero(top & (nw >= 3) & (nd >= 3))):
        o = ok[:, y, x]
        w, l = wet[o, y, x], LV[o]
        if len(l) < 8:
            continue
        c = np.sort(l)
        c = (c[:-1] + c[1:]) / 2
        wrong = np.array([(w & (l < s)).sum() + (~w & (l > s)).sum() for s in c])
        j = int(np.argmin(wrong))
        if wrong[j] <= 0.2 * len(l) and lo < c[j] < hi:
            hs.append(c[j])
            ls.append(L[y, x])
    return (float(np.median(hs)), len(hs), float(np.median(ls))) if len(hs) >= 30 else (None, len(hs), None)


def _bed_out(name, a, Z, valid, meta, out):
    """The depths (Z, m below mean sea level, UTM's 10 m pixels) where valid, on the area's fine grid (tables.FINE x
    SWAN's) over the window: OUT/NAME-bed.json + .bin.gz (area.py bed_for: 2 cm steps from the depths' base, 255 none)."""
    import gzip
    from rasterio.warp import transform

    import tables as T

    box, F = a["box"], T.FINE
    FW, FH = a["W"] * F, a["H"] * F
    x0, y0, x1, y1 = meta["x0"], meta["y0"], meta["x0"] + N * 10, meta["y0"] - N * 10
    lo, la = transform(meta["crs"], "EPSG:4326", [x0, x1, x0, x1], [y0, y0, y1, y1])
    mx = (np.array(lo) + 180) / 360
    my = (1 - np.log(np.tan(np.pi / 4 + np.radians(la) / 2)) / np.pi) / 2
    c0, c1 = (int(np.clip(f((m - box[0]) / box[2] * FW), 0, FW)) for f, m in ((np.floor, mx.min()), (np.ceil, mx.max())))
    r0, r1 = (int(np.clip(f((m - box[1]) / box[3] * FH), 0, FH)) for f, m in ((np.floor, my.min()), (np.ceil, my.max())))
    bw, bh = c1 - c0, r1 - r0
    bbox = [box[0] + c0 / FW * box[2], box[1] + r0 / FH * box[3], bw / FW * box[2], bh / FH * box[3]]
    MX, MY = np.meshgrid(bbox[0] + (np.arange(bw) + 0.5) / bw * bbox[2], bbox[1] + (np.arange(bh) + 0.5) / bh * bbox[3])
    LON, LAT = MX * 360 - 180, np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * MY))))
    ux, uy = transform("EPSG:4326", meta["crs"], list(LON.ravel()), list(LAT.ravel()))
    cc = np.floor((np.array(ux) - x0) / 10).astype(int).reshape(bh, bw)
    rr = np.floor((y0 - np.array(uy)) / 10).astype(int).reshape(bh, bw)
    inside = (rr >= 0) & (rr < N) & (cc >= 0) & (cc < N)
    rr, cc = np.clip(rr, 0, N - 1), np.clip(cc, 0, N - 1)
    z = Z[rr, cc]
    good = inside & valid[rr, cc] & np.isfinite(z) & (z <= 2.5)
    b = np.where(good, np.clip(np.round((np.maximum(z, -0.9) - T.DEPTH_BASE) / T.DEPTH_STEP), 0, 254), 255).astype(np.uint8)
    (out / f"{name}-bed.json").write_text(json.dumps({"box": bbox, "W": bw, "H": bh, "base": T.DEPTH_BASE,
                                                       "step": T.DEPTH_STEP, "note": f"survey.py {time.strftime('%Y-%m-%d')}"}, indent=1))
    (out / f"{name}-bed.bin.gz").write_bytes(gzip.compress(b.tobytes(), 9))
    return int(good.sum())


def survey(name, a, data, out, crest=CREST, red=True):
    from rasterio.warp import transform
    from scipy.ndimage import binary_fill_holes, distance_transform_edt, gaussian_filter, label, uniform_filter

    lat, lon = A_middle(a["box"])
    d = Path(data) / name
    fetch(name, lat, lon, data)
    meta = json.loads((d / "meta.json").read_text())
    G, NI, RB, days = [], [], [], []
    for f in sorted(d.glob("*.npz")):
        if f.stem >= SINCE:
            days.append(f.stem[:10])
            b = np.load(f)["b"]
            v = b.astype(np.float32) * 1e-4
            v[:, b[0] == 0] = np.nan
            G.append(v[0])
            NI.append(v[1])
            RB.append(v[2:4] if len(v) >= 4 else np.full((2, N, N), np.nan, np.float32))
    G, NI, RB = np.array(G), np.array(NI), np.array(RB)
    with np.errstate(invalid="ignore", divide="ignore"):
        wfrac = np.nanmean((G - NI) / (G + NI) > 0, 0)
    lab, _ = label(wfrac > 0.5)
    sea = lab == np.bincount(lab.ravel()[lab.ravel() > 0]).argmax()
    dsea = distance_transform_edt(sea) * 10
    band, off = (dsea > 20) & (dsea < 600), dsea > 1200
    if off.sum() < 500:
        off = sea & (dsea >= np.percentile(dsea[sea], 90))
    nb = np.array([np.nanmean(x[band] > 0.04) for x in NI])
    calm = (nb < 0.01) & (np.array([np.nanmedian(x[off]) for x in NI]) < 0.015) & \
        (np.array([np.nanpercentile(x[off], 95) for x in NI]) < 0.02)
    gdeep = np.array([np.nanpercentile(x[off], 10) for x in G])

    def colour(k):
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.nanmedian(np.log(np.clip(G[k] - gdeep[k, None, None], 1e-4, None)), 0)

    L = colour(calm)
    rdeep = np.array([np.nanpercentile(x[0][off], 10) for x in RB])
    with np.errstate(invalid="ignore", divide="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            Lr = np.nanmedian(np.log(np.clip(RB[calm, 0] - rdeep[calm, None, None], 1e-4, None)), 0)
    gc, nirc = np.nanmedian(G[calm], 0), np.nanmedian(NI[calm], 0)
    water = wfrac >= 0.3
    plants = (nirc / np.maximum(gc, 1e-3) > 1.3) & (wfrac < 0.9) & water  # reeds, grass, algae mats in the water

    # The pixels' middles on our grid (area.py as tables.py builds it, no bars or shallows).
    cc, rr = np.meshgrid(np.arange(N), np.arange(N))
    UX, UY = meta["x0"] + cc * 10 + 5.0, meta["y0"] - rr * 10 - 5.0

    def toLL(x, y):
        return transform(meta["crs"], "EPSG:4326", list(np.ravel(x)), list(np.ravel(y)))

    LON, LAT = (np.array(v).reshape(N, N) for v in toLL(UX, UY))
    box, W, H = a["box"], a["W"], a["H"]
    grid = A.build(box, W, H)
    MY = (1 - np.log(np.tan(np.pi / 4 + np.radians(LAT) / 2)) / np.pi) / 2
    gr = np.clip(((MY - box[1]) / box[3] * H).astype(int), 0, H - 1)
    gcol = np.clip((((LON + 180) / 360 - box[0]) / box[2] * W).astype(int), 0, W - 1)
    gd = np.where(grid[gr, gcol] >= 0, grid[gr, gcol], np.nan)

    dland = distance_transform_edt(water) * 10
    # Narrow water: over 300 m from any water 200 m off land (a beach's edge is near the open sea, a river's isn't).
    river = water & (distance_transform_edt(dland <= 200) * 10 > 300)
    spot = np.hypot(LAT - lat, (LON - lon) * math.cos(math.radians(lat))) * 111195.0
    # Only water joined to the open sea (the review: a bog, lakes and river reaches inland got sea depths), and not the
    # beach's 20 m rim (part sand, part water: on steep coasts with a weak fit half the "under 1 m" read as drying there;
    # area.py's fill gives the rim its neighbours' depths).
    wl, _ = label(water)
    # (water at the window's edge too, 20 ha or more: a bay joined to the sea outside the window or round a spit, the
    # review's Harilaid bay; edge ponds stay out)
    edge = np.unique(np.concatenate([wl[0], wl[-1], wl[:, 0], wl[:, -1]]))
    big = np.bincount(wl.ravel())
    joined = np.union1d(np.unique(wl[sea & water]), [k for k in edge if k and big[k] >= 2000])
    seaw = np.isin(wl, joined) & (wl > 0)
    dom = water & seaw & (dland > 20) & (dland <= 1200) & ~plants & ~river & np.isfinite(L)
    doubts = []
    if calm.sum() < MIN_CALM:
        doubts.append(f"only {int(calm.sum())} calm scenes")
    if dom.sum() < 2000:
        raise SystemExit(f"{name}: too little water near the shore in the window ({int(dom.sum())} pixels)")
    L_crest = float(np.percentile(L[dom & (dland > 30)], 98))
    # (Offshore sand only, 60 m or more from the beach: at Svetciems the brightest was the beach's swash edge, pixels part
    # dry sand, bright by the sand, not the depth; measured there it put the bars 0.3 m too high.)
    lv, gauge = levels_at(lat, lon, days)
    if gauge and "the gauge's zero" in gauge:
        doubts.append(f"levels from {gauge} (Copernicus missing): the crest may be off by the gauge's datum")
    off60 = dom & (dland > 60)
    # (Scenes without whitecaps out at sea only: breaking waves' white water reads as dry sand in the near infrared. Judged
    # far out, not near the shore: there a dry bank is bright in the near infrared too, and the low-water scenes, where
    # Berzciems's bank is dry, were all thrown out as foamy.)
    still = np.array([np.nanmedian(x[off]) for x in NI]) < 0.015
    top_h, top_n, L_top = waterline(G[still], NI[still], [d for d, s in zip(days, still) if s], lv,
                                    off60 & (L >= np.percentile(L[off60], 98)), L) if lv and off60.any() else (None, 0, None)
    if top_h is not None:
        crest, how, L_crest = -top_h, f"measured at {gauge}: {top_n} pixels", L_top
    else:
        how = f"given ({top_n} pixels measured" + (f" at {gauge})" if gauge else ", no gauge)")
    lo, hi = np.percentile(L[dom], [20, 70])
    ok = dom & (gd >= 1.5) & (gd <= 3.0) & (L > lo) & (L < hi)
    if ok.sum() < 50:  # (steep coasts: little 1.5-3 m water, and that in the bright band; one wider try)
        ok = dom & (gd >= 1.0) & (gd <= 5.0) & np.isfinite(gd)
        if ok.sum() >= 50:
            doubts.append(f"anchored on 1-5 m water (little at 1.5-3 m on our grid): {int(ok.sum())} pixels")
    if ok.sum() < 50:
        doubts.append("no 1.5-3 m water on our grid to anchor on")
        raise SystemExit(f"{name}: {doubts[-1]}")
    L_w, D_w = float(np.median(L[ok])), float(np.median(gd[ok]))
    b = (D_w - crest) / (L_w - L_crest)
    a0 = crest - b * L_crest
    two_k = -1 / b if b < 0 else float("inf")
    if not TWO_K[0] <= two_k <= TWO_K[1]:
        doubts.append(f"light's fall-off 2K {two_k:.2f}/m (Baltic water {TWO_K[0]}-{TWO_K[1]})")
    Z = a0 + b * L
    # A dark bottom in shallow water (weed, a reed band's mud) reads as depth in green, but red, which 1-2 m of brown
    # Baltic water swallows, still comes back: where green says over 1 m, red is brighter than in the anchor's 1.5-3 m
    # water and red against green (log) is RED of the way from that water's to the bank sand's, the depth from red
    # against green instead, fitted on the spot's ordinary pixels, never above the measured crest. Each spot's own
    # numbers (Berzciems, 21.11.2026: its near-beach band read 1.5-2.5 m had red - green +0.63, its 1.5-2.5 m water -0.95,
    # the bank's sand -0.03, the owner's lagoon there 0.12 m; Svetciems's water -0.33, sand +0.14). Off where red hardly
    # tells sand from that water (under 0.3 apart). (Open water deeper still has neither colour: red's floor.)
    Dr = Lr - L
    red_w, dr_w = float(np.nanmedian(Lr[ok])), float(np.nanmedian(Dr[ok]))
    dr_s = float(np.nanmedian(Dr[dom & (Z < crest + 0.3)])) if (dom & (Z < crest + 0.3)).any() else np.nan
    reds = np.zeros_like(dom)
    red_on = bool(np.isfinite(dr_s) and dr_s - dr_w >= 0.3)
    if red_on and red:  # (bars.json "red": false where the review found red on plumes or harbour water)
        reds = dom & np.isfinite(Dr) & (Z > 1.0) & (Lr > red_w + 0.3) & (Dr > dr_w + RED * (dr_s - dr_w))
        # (a dark bottom is a patch, not a sprinkle: Svetciems's 1-2.5 m water flickered over the line pixel by pixel)
        reds &= uniform_filter(reds.astype(np.float32), 5) >= 0.5
        # (and the beach's: a patch touching the 150 m along it. The Baltic batch's review, 21.11.2026: at Liepaja's moles
        # and a Tallinn rider spot the rule painted plain water 1-2 km out, red barely over the deep water's; Berzciems's
        # band, Haademeeste's reed shore and trough touch the beach, their red 0.6 (log) over the 1.5-3 m water's)
        reds &= dland <= 200  # (and near it: the review found red on troughs, plumes and harbour water 0.3-2 km out)
        lab, n = label(reds)
        if n:
            near = np.zeros(n + 1, bool)
            near[np.unique(lab[reds & (dland <= 150)])] = True
            near[0] = False
            reds = near[lab]
    fitp = dom & np.isfinite(Dr) & (Z > 0) & (Z < 2.2) & ~reds
    red_ha = 0.0
    if reds.any() and fitp.sum() >= 500:
        c1, c0 = np.polyfit(Dr[fitp], Z[fitp], 1)
        Z = np.where(reds, np.maximum(c0 + c1 * Dr, crest), Z)
        red_ha = float(reds.sum() / 100)
        if red_ha > ((Z <= 1.0) & dom).sum() / 100:
            doubts.append(f"red made {red_ha:.0f} ha shallower but most stayed over 1 m: turbid water, not a bottom?")
    # A dark bottom (submerged weed, stones) reads as depth: a reading DARK m deeper than the median of the ~110 m round
    # it is set to that median (the owner's Berzciems, 21.11.2026: 2.0-2.5 m patches beside 0.2 m sand at the reed band's
    # edge). Troughs between bars stay (long, ~0.5 m deeper); a channel stays (deep round it too).
    dark = _dark(Z, dom)
    Z = np.where(dark, _median_round(Z, dom, dark), Z)
    dark_ha = float(dark.sum() / 100)
    # A wide deep band at the beach where red couldn't judge (red judged it deep elsewhere: a trough, steep coasts' close
    # water): flagged for the review (in the first batch, 21.11.2026, it flagged 35 of 38 spots with red judging).
    band_ha = float((dom & (dland <= 150) & (Z > 1.5)).sum() / 100)
    if not red_on and band_ha >= 1 and (dom & (dland > 150) & (Z < 0.5)).sum() >= 100:
        doubts.append(f"{band_ha:.0f} ha read deeper than 1.5 m within 150 m of the beach, shallower sand outside: a dark "
                      f"bottom (weed) or a real trough?")
    # The scenes' two halves (every other calm scene), on the one fit: the bed they agree on.
    idx = np.nonzero(calm)[0]
    Z1, Z2 = a0 + b * colour(idx[0::2]), a0 + b * colour(idx[1::2])
    shallow = dom & (Z < 1.5)
    split = float(np.nanmedian(np.abs(Z1 - Z2)[shallow])) if shallow.any() else 0.0
    if split > HALVES:
        doubts.append(f"the scenes' halves differ by {split:.2f} m")
    near = water & (dland <= 1200) & (spot <= 2000)
    share = float(plants[near].mean()) if near.any() else 0.0
    if share > PLANTS:
        doubts.append(f"plants on {share:.0%} of the water near the spot")
    river_ha = float((river & (spot <= 1500)).sum() / 100)
    if river_ha >= 10:  # (1-10 ha: ditches, harbour basins; the river mouths here 16-43 ha)
        doubts.append(f"a river or channel near the spot ({river_ha:.0f} ha of narrow water): open it (bars.json river)?")

    stat = {lev: float(((Z <= lev) & dom).sum()) / 100 for lev in LEVELS}
    if two_k < 0.4 and not how.startswith("measured"):
        doubts.append(f"a weak fit (2K {two_k:.2f}/m, the crest given): bars further out may be missing")
    dry, under1 = float(((Z < 0) & dom).sum()) / 100, stat[1.0]
    if under1 > 0 and dry / under1 > 0.3:
        doubts.append(f"{dry:.0f} of the {under1:.0f} ha under 1 m read as drying")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    pixels = _bed_out(name, a, Z, dom, meta, out)
    rgb = np.nanmedian(RB[calm], 0) if np.isfinite(RB[calm]).any() else None
    res = {"area": name, "made": time.strftime("%Y-%m-%d"), "scenes": len(G), "calm": int(calm.sum()),
           "fit": {"a": round(a0, 3), "b": round(b, 3), "2K": round(two_k, 2), "crest": [round(L_crest, 3), round(crest, 2), how],
                   "water": [round(L_w, 3), round(D_w, 2), int(ok.sum())], "halves": round(split, 2)},
           "ha": {f"{k:g}": round(v, 1) for k, v in stat.items()}, "dark_ha": round(dark_ha, 1), "red_ha": round(red_ha, 1), "doubts": doubts,
           "bed": pixels, "sheet": f"{name}.png"}
    (out / f"{name}.json").write_text(json.dumps(res, indent=1))
    _picture(out / f"{name}.png", Z, water, dom | river, plants, river, res, meta, LAT, LON,
             None if rgb is None else (rgb[0], np.nanmedian(G[calm], 0), rgb[1]), dark | reds)
    print(name, {k: res[k] for k in ("scenes", "calm", "fit", "ha", "doubts")}, pixels, "bed pixels", flush=True)
    return res


def _dark(Z, dom):
    """Readings DARK m deeper than the median of the 11 x 11 pixels (~110 m) round them (dom only)."""
    cand = dom & (Z > DARK - 0.5)
    med = _median_round(Z, dom, cand)
    return cand & (Z - med > DARK) & (med < 1.5)  # (a pocket in shallow water; plain deep water's specks left)


def _median_round(Z, dom, where):
    """The median of the 11 x 11 dom pixels round each pixel of where (NaN elsewhere)."""
    from numpy.lib.stride_tricks import sliding_window_view

    out = np.full(Z.shape, np.nan, np.float32)
    ys, xs = np.nonzero(where)
    if len(ys):
        win = sliding_window_view(np.pad(np.where(dom, Z, np.nan), 5, constant_values=np.nan), (11, 11))
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out[ys, xs] = np.nanmedian(win[ys, xs].reshape(len(ys), -1), 1)
    return out


def _picture(path, Z, water, shown, plants, river, res, meta, LAT, LON, colour=None, dark=None):
    """The review sheet (the owner, 21.11.2026, B: a Claude review per spot, the owner only for what it can't settle):
    left the calm scenes' true colour (red, green, blue medians, stretched), right the depth (dark blue deep, pale
    shallow, white above mean sea level; land grey, plants green, river channels brown, dark bottoms corrected tinted magenta),
    under them the fit, the waterline's crest and the doubts; north up (UTM's own grid, near enough), 500 m bar."""
    from PIL import Image, ImageDraw

    t = np.clip((np.nan_to_num(Z, nan=3.0) + 0.25) / 3.25, 0, 1)[..., None]
    rgb = (1 - t) * np.array([235, 245, 250]) + t * np.array([20, 60, 130])
    rgb = np.where(shown[..., None], rgb, np.where(water[..., None], [70, 100, 150], [150, 150, 145]))
    rgb = np.where(plants[..., None], [60, 120, 60], rgb)
    rgb = np.where(river[..., None], [130, 100, 70], rgb)
    if dark is not None:
        rgb = np.where(dark[..., None], 0.55 * rgb + 0.45 * np.array([220, 40, 220]), rgb)  # (tinted: its depth still seen)
    panels = [Image.fromarray(rgb.astype(np.uint8))]
    if colour is not None:
        c = np.stack(colour, -1)
        lo, hi = np.nanpercentile(c, 1), np.nanpercentile(c, 99)
        c = np.clip((np.nan_to_num(c, nan=lo) - lo) / max(hi - lo, 1e-4), 0, 1) ** 0.6 * 255
        panels.insert(0, Image.fromarray(c.astype(np.uint8)))
    S, gap, foot = N + N // 4, 12, 150  # (500 px a panel: 8 m a pixel, the 500 m bar 62 px)
    img = Image.new("RGB", (len(panels) * (S + gap) - gap, S + foot), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    for i, pnl in enumerate(panels):
        x = i * (S + gap)
        img.paste(pnl.resize((S, S), Image.NEAREST), (x, 0))
        dr.line([(x + 12, S - 14), (x + 12 + S * 500 // (N * 10), S - 14)], fill=(0, 0, 0), width=4)
        dr.text((x + 12, S - 30), "500 m", fill=(0, 0, 0))
    f = res["fit"]
    lines = [f"{res['area']}  {res['made']}  {res['calm']} calm of {res['scenes']} scenes  (left: true colour, right: depth)",
             f"crest {f['crest'][1]} m below mean ({f['crest'][2]}); water anchor {f['water'][1]} m; 2K {f['2K']}/m; "
             f"halves {f['halves']} m",
             "ha shallower than: " + ", ".join(f"{k} m {v}" for k, v in res["ha"].items()) +
             f"; dark bottoms: {res.get('red_ha', 0)} ha by red, {res.get('dark_ha', 0)} ha evened (tinted magenta)",
             "doubts: " + ("; ".join(res["doubts"]) or "none")]
    for k, s in enumerate(lines):
        dr.text((10, S + 10 + 22 * k), s, fill=(0, 0, 0))
    img.save(path)


def plan(only):
    """The areas to survey (survey.yml): "baltic" (the index's areas in the Baltic: 53.5-66 N, 10-31 E), "all", or names
    (comma-separated)."""
    import tables as T

    areas = (T._json(T.STORE + "index.json") or {}).get("areas", [])
    if only in ("baltic", "all"):
        return [a["area"] for a in areas
                if only == "all" or (53.5 <= A_middle(a["box"])[0] <= 66 and 10 <= A_middle(a["box"])[1] <= 31)]
    names = {a["area"] for a in areas}
    return [n for n in only.split(",") if n.strip() and n.strip() in names]


def summary(d):
    """The batch's table for the review (survey.yml): SUMMARY.md in d, a row a spot (its sheet AREA.png beside it), the
    doubtful and failed first."""
    rows = []
    for f in sorted(d.glob("*.json")):
        r = json.loads(f.read_text())
        if "fit" in r:
            rows.append((1 if r["doubts"] else 2, r["area"], f"{r['calm']}/{r['scenes']}", f"{r['fit']['crest'][1]:+.2f}",
                         r["fit"]["crest"][2].split(":")[0], r["ha"].get("0.3", 0), r.get("red_ha", 0), r.get("dark_ha", 0),
                         "; ".join(r["doubts"]) or "-"))
    for f in sorted(d.glob("*.failed")):
        rows.append((0, f.stem, "-", "-", "-", "-", "-", "-", "failed: " + " ".join(f.read_text().split())[-300:]))
    rows.sort()
    nl = chr(10)
    head = "| spot | calm/scenes | crest m | crest from | ha under 0.3 m | red ha | evened ha | doubts |" + nl + \
        "|---|---|---|---|---|---|---|---|" + nl
    (d / "SUMMARY.md").write_text(f"# Survey {time.strftime('%Y-%m-%d')}: {len(rows)} spots, "
                                  f"{sum(r[0] == 0 for r in rows)} failed, {sum(r[0] == 1 for r in rows)} doubtful" +
                                  nl + nl + head + "".join("| " + " | ".join(str(x) for x in r[1:]) + " |" + nl for r in rows))
    print((d / "SUMMARY.md").read_text())


def A_middle(box):
    """The box's middle (lat, lon): the area's first spot."""
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2)))))
    return lat, (box[0] + box[2] / 2) * 360 - 180


def apply(name, out, force=False, keep_bars=False):
    """The survey's bed into beds/ and bars.json "bed": it replaces the rule's bars (crest depths by rule) and the
    survey's own earlier contour shapes there; the owner's own shapes (lagoons...) kept; the river not opened (the
    owner's call)."""
    import shutil

    res = json.loads((Path(out) / f"{name}.json").read_text())
    if res["doubts"] and not force:
        raise SystemExit(f"{name}: doubtful, for the owner: {res['doubts']}")
    (HERE / "beds").mkdir(exist_ok=True)
    for ext in ("json", "bin.gz"):
        shutil.copy(Path(out) / f"{name}-bed.{ext}", HERE / "beds" / f"{name}.{ext}")
    p = HERE / "bars.json"
    bars = json.loads(p.read_bytes().decode("utf-8"))
    e = bars.setdefault(name, {})
    if not keep_bars:  # (kept by default since 21.11.2026: under the bed, they hold the bars past its ~2 m, Pavilosta's
        e.pop("bars", None)  # second at 2.2 m; Lielupe's towards Bulduri: the owner, 20.11.2026, "leave it")
    kept = [s for s in e.get("shallows", []) if not s["name"].startswith(f"{name} ")]
    if kept:
        e["shallows"] = kept
    else:
        e.pop("shallows", None)
    e["bed"] = name
    e["survey"] = {k: res[k] for k in ("made", "scenes", "calm", "fit", "doubts")}
    p.write_bytes((json.dumps(bars, indent=1) + "\n").encode("utf-8"))
    print(name, "into beds/ and bars.json:", res["bed"], "bed pixels,", len(kept), "of the owner's shapes kept")


if __name__ == "__main__":
    args = sys.argv[1:]

    def opt(k, d):
        return args[args.index(k) + 1] if k in args else d

    name = args[0]
    out = opt("--out", str(HERE / "survey"))
    if name == "plan":
        print(json.dumps(plan(opt("--only", "baltic"))))
    elif name == "summary":
        summary(Path(args[1]))
    elif "--apply" in args:
        apply(name, out, "--force" in args, "--drop-bars" not in args)
    else:
        import tables as T

        a = next((x for x in (T._json(T.STORE + "index.json") or {}).get("areas", []) if x["area"] == name), None)
        if a is None:
            raise SystemExit(f"{name}: not in the index")
        survey(name, a, opt("--data", str(Path.home() / "swan" / "sat")), out, float(opt("--crest", (T.bars_for(name) or {}).get("crest", CREST))),
               bool((T.bars_for(name) or {}).get("red", True)))
