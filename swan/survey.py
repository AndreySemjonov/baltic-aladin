"""A spot's shallows from the satellite: the automatic survey's depth step (the owner, 20.11.2026, Svetciems's satellite
picture: "we should include this somehow in automatic process of detailed spot when some spot comes as official ... in
Svetciems we have sandbars too what make flat water on low water level. But after all live we do not have it", then
"we will make svetciems later as pilot of automatic spot process"; WindguruStationWatch docs/backlog/design.md "SWAN spots").

Sentinel-2 L2A (earth-search on AWS, no account; its values already have the offset in), a 4 x 4 km window round the
area's spot, 2023 on (bars move), clear scenes outside the ice months. Calm scenes (no foam or glint near the shore, a
dark open sea) give the water's colour, the median of their ln(green - the deep water's green): brighter is shallower.
Two anchors set its scale (depth = a + b L): the brightest water (p98, off the beach's mixed pixels) is the bars' crest,
CREST below mean sea level (-0.25: dry below that level), and ordinary water where our depth grid (area.py, no bars) has
1.5-3 m is that deep. Contoured at LEVELS into bars.json "shallows" (area.py _shallows: depth caps, never deeper than the
grid); river channels (narrow water) and plants left out. A river joined to the sea near the spot is reported, not opened.

Doubtful, so it waits for the owner (the owner, 20.11.2026: "Confident parts go live straight away"): few calm scenes, no
1.5-3 m water to anchor on, a light fall-off with depth (2K) out of what clear to brown Baltic water gives, the two
halves of the scenes disagreeing, many plants, a river. Limits: an estimate; the Gulf of Riga's brown water and dark
bottoms blur it below ~1.5-2 m; the scenes' water levels differ (the median's level is roughly mean sea level).

  python3 swan/survey.py AREA [--data DIR] [--out DIR] [--crest M]   the scenes into DIR/AREA (kept), OUT/AREA.json
                                                                     (the shallows, the fit, the doubts) and AREA.png
  python3 swan/survey.py AREA --apply [--force]                      that survey into bars.json (doubtful: only --force)
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
LEVELS = (-0.25, 0.1, 0.3, 0.6, 1.0)
CREST = -0.25
MIN_CALM, TWO_K, HALVES, PLANTS = 8, (0.15, 1.5), 0.25, 0.2


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
        if all(k in a for k in ("green", "nir", "scl")):
            tile = f"{p.get('mgrs:utm_zone')}{p.get('mgrs:latitude_band')}{p.get('mgrs:grid_square')}"
            byday[(p["datetime"][:10], tile)] = {"date": p["datetime"][:10], "tile": tile,
                                                 **{k: a[k]["href"] for k in ("green", "nir", "scl")}}
    return list(byday.values())


def fetch(name, lat, lon, data):
    """The window's green and near-infrared (10 m) from every scene under 3 % cloud there, into data/name (kept: a
    rerun fetches only new scenes)."""
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
        if f.exists():
            return "have"
        try:
            with rasterio.Env(**ENV):
                with rasterio.open(it["scl"]) as d:
                    if np.isin(d.read(1, window=swin), [0, 3, 8, 9, 10, 11]).mean() > 0.03:
                        return "cloudy"
                bands = []
                for k in ("green", "nir"):
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


def survey(name, a, data, out, crest=CREST):
    from rasterio.warp import transform
    from scipy.ndimage import binary_fill_holes, distance_transform_edt, gaussian_filter, label

    lat, lon = A_middle(a["box"])
    d = Path(data) / name
    if not (d / "meta.json").exists():
        fetch(name, lat, lon, data)
    meta = json.loads((d / "meta.json").read_text())
    G, NI = [], []
    for f in sorted(d.glob("*.npz")):
        if f.stem >= SINCE:
            b = np.load(f)["b"]
            v = b.astype(np.float32) * 1e-4
            v[:, b[0] == 0] = np.nan
            G.append(v[0])
            NI.append(v[1])
    G, NI = np.array(G), np.array(NI)
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
    dom = water & (dland <= 1200) & ~plants & ~river & np.isfinite(L)
    doubts = []
    if calm.sum() < MIN_CALM:
        doubts.append(f"only {int(calm.sum())} calm scenes")
    if dom.sum() < 2000:
        raise SystemExit(f"{name}: too little water near the shore in the window ({int(dom.sum())} pixels)")
    L_crest = float(np.percentile(L[dom & (dland > 30)], 98))
    lo, hi = np.percentile(L[dom], [20, 70])
    ok = dom & (gd >= 1.5) & (gd <= 3.0) & (L > lo) & (L < hi)
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
    if river_ha >= 1:
        doubts.append(f"a river or channel near the spot ({river_ha:.0f} ha of narrow water): open it (bars.json river)?")

    Zs = gaussian_filter(np.where(dom, Z, 5.0), 1.0)
    shallows, stat = [], {}
    for lev in LEVELS:
        lab2, n2 = label((Zs <= lev) & dom & (dland > (20 if lev < 0 else 0)))
        k = 0
        for q in range(1, n2 + 1):
            comp = binary_fill_holes(lab2 == q)
            if comp.sum() * 100 < (500 if lev < 0 else 2000):
                continue
            k += 1
            shallows.append({"name": f"{name} {lev:g} #{k}", "depth": lev, "ring": _ring(comp, meta, toLL)})
            stat[lev] = stat.get(lev, 0.0) + float(comp.sum()) / 100
    res = {"area": name, "made": time.strftime("%Y-%m-%d"), "scenes": len(G), "calm": int(calm.sum()),
           "fit": {"a": round(a0, 3), "b": round(b, 3), "2K": round(two_k, 2), "crest": [round(L_crest, 3), crest],
                   "water": [round(L_w, 3), round(D_w, 2), int(ok.sum())], "halves": round(split, 2)},
           "ha": {f"{k:g}": round(v, 1) for k, v in stat.items()}, "doubts": doubts, "shallows": shallows}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(res, indent=1))
    _picture(out / f"{name}.png", Z, water, dom | river, plants, river, res, meta, LAT, LON)
    print(name, {k: res[k] for k in ("scenes", "calm", "fit", "ha", "doubts")}, len(shallows), "shapes", flush=True)
    return res


def _picture(path, Z, water, shown, plants, river, res, meta, LAT, LON):
    """The check: depth (dark blue deep, pale shallow, white above mean sea level), land grey, plants green, river
    channels brown, the shapes' outlines by level; north up (UTM's own grid, near enough)."""
    from PIL import Image, ImageDraw

    t = np.clip((np.nan_to_num(Z, nan=3.0) + 0.25) / 3.25, 0, 1)[..., None]
    rgb = (1 - t) * np.array([235, 245, 250]) + t * np.array([20, 60, 130])
    rgb = np.where(shown[..., None], rgb, np.where(water[..., None], [70, 100, 150], [150, 150, 145]))
    rgb = np.where(plants[..., None], [60, 120, 60], rgb)
    rgb = np.where(river[..., None], [130, 100, 70], rgb)
    img = Image.fromarray(rgb.astype(np.uint8)).resize((2 * N, 2 * N), Image.NEAREST)
    dr = ImageDraw.Draw(img)
    cols = {-0.25: (255, 255, 255), 0.1: (255, 160, 0), 0.3: (0, 220, 220), 0.6: (40, 90, 255), 1.0: (230, 0, 230)}
    from rasterio.warp import transform

    for s in res["shallows"]:
        x, y = transform("EPSG:4326", meta["crs"], [p[1] for p in s["ring"]], [p[0] for p in s["ring"]])
        dr.line([((u - meta["x0"]) / 5, (meta["y0"] - v) / 5) for u, v in zip(x, y)], fill=cols[s["depth"]], width=2)
    dr.line([(20, 2 * N - 20), (120, 2 * N - 20)], fill=(0, 0, 0), width=4)
    dr.text((20, 2 * N - 40), "500 m", fill=(0, 0, 0))
    dr.text((10, 10), f"{res['area']}: {res['calm']} calm scenes, 2K {res['fit']['2K']}/m; caps " +
            " ".join(f"{k}m" for k in res["ha"]), fill=(0, 0, 0))
    img.save(path)


def A_middle(box):
    """The box's middle (lat, lon): the area's first spot."""
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2)))))
    return lat, (box[0] + box[2] / 2) * 360 - 180


def apply(name, out, force=False):
    """The survey's shallows into bars.json: they replace the rule's bars (crest depths by rule) there; the river not
    opened (the owner's call)."""
    res = json.loads((Path(out) / f"{name}.json").read_text())
    if res["doubts"] and not force:
        raise SystemExit(f"{name}: doubtful, for the owner: {res['doubts']}")
    p = HERE / "bars.json"
    bars = json.loads(p.read_bytes().decode("utf-8"))
    e = bars.setdefault(name, {})
    e.pop("bars", None)
    e["shallows"] = res["shallows"]
    e["survey"] = {k: res[k] for k in ("made", "scenes", "calm", "fit", "doubts")}
    p.write_bytes((json.dumps(bars, indent=1) + "\n").encode("utf-8"))
    print(name, "into bars.json:", len(res["shallows"]), "shapes")


if __name__ == "__main__":
    args = sys.argv[1:]

    def opt(k, d):
        return args[args.index(k) + 1] if k in args else d

    name = args[0]
    out = opt("--out", str(HERE / "survey"))
    if "--apply" in args:
        apply(name, out, "--force" in args)
    else:
        import tables as T

        a = next((x for x in (T._json(T.STORE + "index.json") or {}).get("areas", []) if x["area"] == name), None)
        if a is None:
            raise SystemExit(f"{name}: not in the index")
        survey(name, a, opt("--data", str(Path.home() / "swan" / "sat")), out, float(opt("--crest", CREST)))
