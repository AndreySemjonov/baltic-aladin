"""A spot's shallows from the satellite: the automatic survey's depth step (the owner, 20.11.2026, Svetciems's satellite
picture: "we should include this somehow in automatic process of detailed spot when some spot comes as official ... in
Svetciems we have sandbars too what make flat water on low water level. But after all live we do not have it", then
"we will make svetciems later as pilot of automatic spot process"; WindguruStationWatch docs/backlog/design.md "SWAN spots").

Sentinel-2 L2A (earth-search on AWS, no account; its values already have the offset in), a 4 x 4 km window round the
area's spot, 2023 on (bars move), clear scenes outside the ice months. Calm scenes (no foam or glint near the shore, a
dark open sea) give the water's colour, the median of their ln(green - the deep water's green): brighter is shallower.
Since 23.11.2026 each pixel's bright times instead (bright(): the 75th percentile over the scenes without whitecaps out
at sea, against each scene's bright sand): a summer bottom of weed read as depth.
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
it evened to that; a wide deep band at the beach where red can't judge taken as stones and weed by default: left out,
filled from round it (bars.json "weed": false keeps a known trough). A river joined to the sea near the spot is
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
                                                                     --force; the rule's bars dropped, --keep-bars keeps them)
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
import tide  # noqa: E402
from tide import TIDAL  # noqa: E402

HERE = Path(__file__).resolve().parent
STAC = "https://earth-search.aws.element84.com/v1/search"
ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES", GDAL_HTTP_MAX_RETRY="4",
           GDAL_HTTP_RETRY_DELAY="3", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")
# The window, 10 m pixels: the whole SWAN area (tables.py SIDE_M 8.7 km) and a margin for UTM's tilt. 4 km before: a spot
# in an area's middle 80 % gets no area of its own (tables.py plan), so Puunsi, 2.4 km from its area's middle, had the
# old ~100 m depth tiles' 2-4 m over its flats (the owner, 22.11.2026: "I want to be sure that our automatic spot process
# works correctly", then "Yes, A + C"); Katariina, Lilaste, Salacgriva and Russalka the same.
N = 900
SINCE = "2023-01-01"
LEVELS = (-0.25, 0.1, 0.3, 0.6, 1.0)  # (the picture's and the report's bands)
GAUGES = "https://erddap.emodnet-physics.eu/erddap/tabledap/ERD_EP_SLEV_NRT_60m.csv?"
CREST = 0.30  # (the waterline's median over the Baltic batch's 31 measured spots, 21.11.2026: 0.17-0.42 m below mean)
MIN_CALM, TWO_K, HALVES, PLANTS = 8, (0.15, 1.5), 0.25, 0.2
BRIGHT = 75  # (each pixel's colour: this percentile of its still scenes, survey's bright())
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
        if f.exists() and np.load(f)["b"].shape == (len(BANDS), N, N):  # (a kept scene of another window fetched again)
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
        # (The sea cell with the most hours: the newest hours are NaN while the dataset updates, so "every hour" found
        # no cell and the Gulf of Riga fell back to the gauges, 0.3 m off the map's zero (22.11.2026, 20:17Z batch).)
        hours = np.isfinite(v).sum(0).reshape(-1)
        if not hours.any():
            return {}
        cell = int(np.argmax(hours))
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
    too high on the map; on the model's zero Svetciems's bars dry at -0.28, the owner's "around -0.3".)
    On a tidal coast (tide.py reach) the tide at the overpass instead: Sentinel-2 crosses at ~10:30 local solar time,
    and the tide moves the water up to a metre in hours there, more than the 09-11 UTC mean can hold (the owner,
    22.11.2026: "far spots we have to make automatic too ... take into account tides"). No surge in it (+-0.1-0.3 m)."""
    if tide.reach(lat, lon) >= TIDAL:
        import calendar

        at = [calendar.timegm(time.strptime(d, "%Y-%m-%d")) + (10.5 - lon / 15) * 3600 for d in days]
        lv = tide.tide(lat, lon, at)
        return {d: float(v) for d, v in zip(days, lv)}, "EOT20's tide at the overpass (the map's)"
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


DRY_NIR = 0.02  # (dry ground's near-infrared: the Lielupe's banks 0.13-0.42, its dark water 0.01-0.02)


def wet_dry(g, n):
    """A scene's pixels wet (green over near-infrared) and dry (not, and bright in near-infrared as sand, mud or grass are);
    neither where too dark to say: the Lielupe's brown water under a low October sun (0.0001-0.006 both) read "dry" in 4
    scenes of 80, and the waterline put the river at -0.4 m, "dry at -0.55" (the owner, 23.11.2026)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        nd = (g - n) / (g + n)
    return nd > 0, (nd <= 0) & (n >= DRY_NIR)


def waterline(G, NI, days, lv, top, L):
    """The brightest offshore sand's height (m above the map's zero, levels_at) from the scenes' wet and dry at their levels
    (levels_at): each pixel of top (a mask) sometimes dry, sometimes wet, at the level that splits them with at most a
    fifth of its scenes on the wrong side, inside the levels seen; the median, how many, and their colour's median (L):
    the fit's anchor. None under 30 pixels."""
    k = [i for i, d in enumerate(days) if d in lv]
    if len(k) < 12:  # (three values, as the caller takes them: the Stor's few still scenes crashed it, 22.11.2026)
        return None, 0, None
    LV = np.array([lv[days[i]] for i in k])
    wet, dry = wet_dry(G[k], NI[k])
    ok = wet | dry
    nw, nd = wet.sum(0), dry.sum(0)
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


def flat_heights(G, NI, days, lv, cand, reach):
    """Each candidate pixel's height (m above mean sea level) by the waterline method, the brightest sand's rule
    (waterline) for every pixel that dries: its scenes wet or dry (green over near-infrared) sorted by their level, the
    height the level splitting them best, at most a fifth on the wrong side, 3+ of each, inside the levels seen and the
    tide's reach. All at once: running counts of wet and dry over the sorted scenes give every split's misses (a big
    tide's flats are ~10^5 pixels). NaN where none. On a tidal coast the flats dry every low tide and stand up to 3-4 m
    above mean sea level on a 6-8 m one; the brightest sand alone gave one crest (the owner, 22.11.2026: "Big tides
    first")."""
    k = [i for i, d in enumerate(days) if d in lv]
    out = np.full(cand.shape, np.nan, np.float32)
    if len(k) < 12 or not cand.any():
        return out
    LV = np.array([lv[days[i]] for i in k])
    order = np.argsort(LV)
    LV = LV[order]
    rr, cc = np.nonzero(cand)
    g, n = G[np.array(k)[order]][:, rr, cc], NI[np.array(k)[order]][:, rr, cc]
    wet, dry = wet_dry(g, n)
    ok = wet | dry
    W, D = wet.astype(np.int16), dry.astype(np.int16)
    cw, cd = np.cumsum(W, 0)[:-1], np.cumsum(D, 0)[:-1]  # (wet and dry among the scenes below each split)
    wrong = cw + (D.sum(0) - cd)  # (wet below the split, dry above it)
    # (The middle of the splits within one miss of the best: with misread scenes several score nearly alike, and the
    # first best jumped up to 0.7 m in the test's trials.)
    least = wrong.min(0)
    mids = (LV[:-1] + LV[1:]) / 2
    with np.errstate(invalid="ignore"):
        h = np.nanmedian(np.where(wrong <= least + 1, mids[:, None], np.nan), 0)
    seen = ok.sum(0)
    # (Dry in under 5 % of the scenes only when every dry one is below every wet one: a lagoon that uncovers only on the
    # year's lowest water, Berzciems's at -0.4..-0.47 (wet in 97-98 % of its scenes); misread scenes, a cloud's shadow or
    # a river's brown flood, don't line up with the lowest levels. The fifth on the wrong side let a river with 3 such
    # scenes in 80 through at ~-0.4 m: the Lielupe "dry at -0.55" (the owner, 23.11.2026).)
    nd = D.sum(0)
    good = (((least <= 0.2 * seen) & (nd >= np.maximum(3, 0.05 * seen))) | ((least == 0) & (nd >= 2))) & (W.sum(0) >= 3) & \
        (h > LV[0]) & (h < LV[-2]) & (np.abs(h) <= reach + 0.3)
    out[rr[good], cc[good]] = h[good]
    # (Each with its 3 x 3 neighbours' median where 5+ have a height: flats are smooth over 10 m, a misread pixel isn't;
    # in the test's trials with a tenth of the scenes misread the worst pixel went from 0.55 m off to ~0.2.)
    pad = np.pad(out, 1, constant_values=np.nan)
    stack = np.stack([pad[1 + dy:1 + dy + out.shape[0], 1 + dx:1 + dx + out.shape[1]] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
    with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore")
        med = np.nanmedian(stack, 0)
    return np.where(np.isfinite(out) & (np.isfinite(stack).sum(0) >= 5), med, out).astype(np.float32)


def flat_gaps(Z, flat, gap, floor, r=4, inside=0.6):
    """Water the waterline couldn't measure (gap: wet in nearly every scene) inside measured flats (inside: that share of
    the (2r+1)^2 pixels round it measured, so a channel or the flats' edge to deeper water isn't one) held at most as deep
    as the deeper of floor (the lowest level seen below the map's zero: it stayed wet there, so it's at least that deep)
    and its measured neighbours' deepest: the colour's extra depth there is a dark bottom (Berzciems's lagoon: 0.3 / 1.0
    speckles where its mud never dried at -0.47 m). (Z, how many)."""
    from scipy.ndimage import maximum_filter, uniform_filter

    share = uniform_filter(flat.astype(np.float32), 2 * r + 1, mode="constant")
    deep = maximum_filter(np.where(flat, Z, -np.inf), 2 * r + 1, mode="constant", cval=-np.inf)
    cap = np.maximum(floor, deep)
    hit = gap & (share >= inside) & np.isfinite(Z) & (Z > cap)
    return np.where(hit, cap, Z), int(hit.sum())


def seasonal(G, NI, days, sea, flat, reach=1500, shore=20, swing=0.0, least=50, lv=None, tide_reach=1.0):
    """Water that comes and goes by the season, not the tide (the owner, 23.11.2026, Tarifa's Los Lances: "in reality
    this lagoon go up and down", then "Yes, build B"): its wettest strip in 78 scenes wet 60-90 % Oct-Apr, 0-5 % Jun-Sep,
    and no link to the tide (-0.03), so the waterline finds no split there; over all 253 scenes 2023-2026 its 39 ha wet in
    ~10 % (2026's winter 14 %): mostly dry, where the base map draws it full. Each pixel off the sea (shore metres or more
    from it, reach at most), not a measured flat, wet in 2-95 % of 20+ readable scenes (never wet: land or plants), its
    wet share by month (the month's scenes twice, its neighbours' once: Feb had 1 scene there; 4+ such readings), where
    the months differ by swing or more (none: a lagoon wet in few scenes all year is drawn dry all year, which it is; a
    0.4 swing kept 1.6 of its 39 ha), in patches of least pixels or more. Plane 12, never wet: 0.5 bare ground, 1 where
    every readable scene saw plants and never water (wet under 2 %, near-infrared over 0.1 and 1.3 x the green): the base map's "Lagoon de Los Lances" is
    that in all 253 scenes (a reed or salt marsh; water under plants can't be seen), its seasonal water a strip behind the
    beach the base map draws as sand (the owner, then "Yes, do A": the strip as water when wet, the marsh as marsh). The
    map draws them on cells our grid calls land (tables.py map_months). (13, N, N), NaN none; None if none."""
    from scipy.ndimage import distance_transform_edt, label

    wet, dry = wet_dry(G, NI)
    ok = wet | dry
    seen = ok.sum(0)
    share = wet.sum(0) / np.maximum(seen, 1)
    # (The sea here: the largest water wet in 95 %+ of the scenes. The survey's own, wet in half, took in Los Lances's wetter
    # channel through its mouth in mm-xqcm8yev's window, a 50-70 m band left without months; the beach's swash this lets in
    # falls on cells our grid calls sea, which tables.py map_months leaves out.)
    lab0, _ = label((share > 0.95) & (seen >= 20))
    core = lab0 == np.bincount(lab0.ravel()[lab0.ravel() > 0]).argmax() if lab0.max() else sea
    dist = distance_transform_edt(~core) * 10
    near = (dist >= shore) & (dist <= reach) & (seen >= 20)  # (flats too: on cells our grid calls land a bed never shows them)
    cand = near & (share >= 0.02) & (share <= 0.95)
    with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore")
        nir, g = np.nanmedian(NI, 0), np.nanmedian(G, 0)
    never = near & (share < 0.02)
    plants = never & (nir > 0.1) & (nir > 1.3 * g)
    if not cand.any() and not never.any():
        return None
    mo = np.array([int(d[5:7]) - 1 for d in days])
    rr, cc = np.nonzero(cand)
    # (Recent scenes count more, half-life 6 months: a channel moves each winter (the owner, 23.11.2026, Los Lances: "it
    # changed its shape all the time"); the last 12 months' scenes there from the earlier ones' months 81 % right, all
    # years equal 76 %.)
    t = np.array([time.mktime(time.strptime(d[:10], "%Y-%m-%d")) for d in days])
    rw = (0.5 ** ((t.max() - t) / (86400 * 182.6))).astype(np.float32)[:, None]
    W = np.stack([(wet[mo == m][:, rr, cc] * rw[mo == m]).sum(0) for m in range(12)]).astype(np.float32)
    O = np.stack([(ok[mo == m][:, rr, cc] * rw[mo == m]).sum(0) for m in range(12)]).astype(np.float32)
    N = np.stack([ok[mo == m][:, rr, cc].sum(0) for m in range(12)]).astype(np.float32)
    Ws, Os = 2 * W + np.roll(W, 1, 0) + np.roll(W, -1, 0), 2 * O + np.roll(O, 1, 0) + np.roll(O, -1, 0)
    Ns = 2 * N + np.roll(N, 1, 0) + np.roll(N, -1, 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        S = np.where((Ns >= 4) & (Os > 0), Ws / Os, np.nan)
        keep = (np.nanmax(S, 0) - np.nanmin(S, 0)) >= swing
    cand[rr[~keep], cc[~keep]] = False
    lab, _ = label(cand)
    big = np.bincount(lab.ravel()) >= least
    big[0] = False
    out = np.full((14,) + sea.shape, np.nan, np.float32)
    stay = big[lab[rr, cc]] & keep
    out[:12, rr[stay], cc[stay]] = S[:, stay]
    # (Never wet: 1 plants, 0.5 bare ground: the lagoon's dry mud read as the open sea's blue on the map without it.)
    out[12][never] = 0.5
    out[12][plants] = 1.0
    # (Wet in nearly every scene: 0.1, water on our shore; a channel the base map may draw as land, the owner's "Ok with A".)
    out[12][near & (share > 0.95)] = 0.1
    # Plane 13, the tide's height that floods it in its open months (the owner, 23.11.2026, Los Lances: "this lagoon
    # changes with tide up and down every day. I am every year go to tarifa in january and I know it"): over all months
    # its wetness had no link to the tide (-0.03), but in Oct-Apr +0.44 at the pass and -0.48 six hours before; summer
    # shuts it (+0.12). The open months: where the patch's median share is half its best month's or more (the season each
    # coast's own, the southern hemisphere's too); the waterline (flat_heights) on those months' scenes only. A quarter of
    # Los Lances's pixels split clean (most at ~+0.5 m: the higher tides); the rest keep their months. Stored pre-scaled
    # for _bed_out's x 254: the byte is the height x 50 + 127 (2 cm steps, +-2.5 m), 255 none.
    if lv and stay.any():
        with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
            __import__("warnings").simplefilter("ignore")
            med = np.nanmedian(S[:, stay], 1)
        if np.isfinite(med).any() and np.nanmax(med) > 0:
            open_m = np.nonzero(np.nan_to_num(med) >= 0.5 * np.nanmax(med))[0]
            k = np.isin(mo, open_m)
            mask = np.zeros_like(cand)
            mask[rr[stay], cc[stay]] = True
            h = flat_heights(G[k], NI[k], [d for d, s in zip(days, k) if s], lv, mask, tide_reach)
            # (Where the open months didn't split it, all the scenes: the beach's face is tidal all year, and on its months
            # alone it stood as water at any level on the map, a band stranded on the sand at low water; Los Lances.)
            rest = mask & ~np.isfinite(h)
            if rest.any():
                h = np.where(np.isfinite(h), h, flat_heights(G, NI, days, lv, rest, tide_reach))
            fin = np.isfinite(h)
            # (Kept only where the tide explains the water better than the month does: at Los Lances the tide's heights
            # put 74 % of the scenes' wet and dry right, the month's picture more; the river and the channel's moves
            # drive it (the owner, 23.11.2026: "not only dependent on high tides, but how much river water"), and a level
            # that moved its water there would claim what we don't know. Andalucia's laser terrain + the tide: 64 %.)
            if fin.any():
                L = np.array([lv.get(d, np.nan) for d in days])
                col = np.full(sea.shape, -1)
                col[rr, cc] = np.arange(len(rr))
                fr, fc = np.nonzero(fin)
                s = np.isfinite(L)
                wt, dr = wet[s][:, fr, fc], dry[s][:, fr, fc]
                sm_ = np.nan_to_num(S[mo[s]][:, col[fr, fc]])
                by_h = (sm_ >= 0.05) & (L[s][:, None] > h[fr, fc][None, :])  # (the map's rule: swan.js ourWater)
                by_m = sm_ >= 0.5
                n_ok = max((wt | dr).sum(), 1)
                sh, sm = ((by_h & wt) | (~by_h & dr)).sum() / n_ok, ((by_m & wt) | (~by_m & dr)).sum() / n_ok
                print(f"seasonal: the tide's heights {sh:.0%} right, the month's picture {sm:.0%}: "
                      f"{'heights kept' if sh >= sm + 0.03 else 'heights dropped'}", flush=True)
                if sh < sm + 0.03:
                    fin[:] = False
            out[13][fin] = np.clip(np.round(h[fin] * 50) + 127, 0, 254) / 254
    return out if stay.any() or never.any() else None


def _bed_out(name, a, Z, valid, meta, out, months=None):
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
    # (The area's own range, tables.py depth_scale: on a tidal coast flats above mean sea level as high as they stand.)
    base, step = T.depth_scale(*A_middle(a["box"]))
    b = np.where(good, np.clip(np.round((z - base) / step), 0, 254), 255).astype(np.uint8)
    (out / f"{name}-bed.json").write_text(json.dumps({"box": bbox, "W": bw, "H": bh, "base": base,
                                                       "step": step, "note": f"survey.py {time.strftime('%Y-%m-%d')}"}, indent=1))
    (out / f"{name}-bed.bin.gz").write_bytes(gzip.compress(b.tobytes(), 9))
    # (The seasonal water's months on the same grid: OUT/NAME-months.bin.gz, 12 planes, the share x 254, 255 none.)
    if months is not None:
        m = months[:, rr, cc]
        mb = np.where(inside & np.isfinite(m), np.round(np.nan_to_num(m) * 254), 255).astype(np.uint8)
        if (mb < 255).any():
            (out / f"{name}-months.bin.gz").write_bytes(gzip.compress(mb.tobytes(), 9))
            (out / f"{name}-months.json").write_text(json.dumps({"box": bbox, "W": bw, "H": bh, "note": f"survey.py {time.strftime('%Y-%m-%d')}"}))
    return int(good.sum())


LASER_MIN = 40  # pixels with the laser's seabed in the satellite's 0.3-2 m to fit on (fewer: only checked against)


def laser_points(LAT, LON):
    """ICESat-2's seabed points over the window (icesat.py by_sliderule: NASA's ATL24 through SlideRule, no login), []
    when SlideRule fails: the survey goes on as before (the owner, 22.11.2026: "Anchor on the laser")."""
    import icesat

    try:
        return icesat.by_sliderule((float(LON.min()), float(LAT.min()), float(LON.max()), float(LAT.max())))
    except Exception as error:
        print("laser:", error, flush=True)
        return []


def laser_pixels(pts, meta):
    """The laser's points on the window's pixels: (rows, cols, depth below mean sea level) a pixel (the median of its
    points), and that mean: the median over the passes of each pass's median surface (depth + the seabed's height:
    the water's top the laser saw that day; ~90 passes over seven years at Rohuneeme, so the long mean, as the map's)."""
    from rasterio.warp import transform

    pts = [p for p in pts if len(p) > 6 and p[6] is not None]
    if not pts:
        return None
    surf, when = {}, {}
    for p in pts:
        surf.setdefault((p[3], p[4]), []).append(p[2] + p[6])
        if len(p) > 7:
            when[(p[3], p[4])] = p[7]
    # (On a tidal coast each pass's surface less the tide at its moment (tide.py): Cape Town's passes stray +-1 m.)
    lat, lon = float(np.median([p[0] for p in pts])), float(np.median([p[1] for p in pts]))
    keys = sorted(surf)
    tides = np.zeros(len(keys))
    if len(when) == len(keys) and tide.reach(lat, lon) >= TIDAL:
        tides = tide.tide(lat, lon, [when[k] for k in keys])
    msl = float(np.median([np.median(surf[k]) - t for k, t in zip(keys, tides)]))
    xs, ys = transform("EPSG:4326", meta["crs"], [p[1] for p in pts], [p[0] for p in pts])
    c = np.floor((np.array(xs) - meta["x0"]) / 10).astype(int)
    r = np.floor((meta["y0"] - np.array(ys)) / 10).astype(int)
    z = msl - np.array([p[6] for p in pts])
    # (Each pass in one of two halves, every other one by time: one half corrects, the other checks, laser_correct.)
    half = {k: i % 2 for i, k in enumerate(sorted(surf))}
    g = np.array([half[(p[3], p[4])] for p in pts])
    inside = (r >= 0) & (r < N) & (c >= 0) & (c < N)
    cells = {}
    for rr, cc, zz, gg in zip(r[inside], c[inside], z[inside], g[inside]):
        cells.setdefault((int(rr), int(cc), int(gg)), []).append(float(zz))
    rc = np.array(list(cells.keys()), int).reshape(-1, 3)
    return {"rows": rc[:, 0], "cols": rc[:, 1], "half": rc[:, 2], "z": np.array([np.median(v) for v in cells.values()]),
            "msl": msl, "passes": len(surf), "points": len(pts)}


LASER_NEAR, LASER_FAR, LASER_SIGMA = 300.0, 1000.0, 300.0  # m: full, none, the smoothing along and across a track


def laser_correction(shape, rows, cols, miss):
    """The laser's misses (its depth minus ours, capped at 1.5 m: a boulder, a stray photon) spread round its tracks:
    a Gaussian mean over ~LASER_SIGMA, full within LASER_NEAR of a laser pixel, none past LASER_FAR. One colour-to-depth
    line for a 9 km window fits its sandy parts and missed Puunsi's darker, stony flats by up to a metre (the owner,
    22.11.2026: "build B")."""
    from scipy.ndimage import distance_transform_edt, gaussian_filter

    R, Wt = np.zeros(shape, np.float32), np.zeros(shape, np.float32)
    np.add.at(R, (rows, cols), np.clip(miss, -1.5, 1.5))
    np.add.at(Wt, (rows, cols), 1.0)
    num, den = gaussian_filter(R, LASER_SIGMA / 10), gaussian_filter(Wt, LASER_SIGMA / 10)
    fade = np.clip((LASER_FAR - distance_transform_edt(Wt == 0) * 10) / (LASER_FAR - LASER_NEAR), 0, 1)
    return np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0.0) * fade


def laser_fit(Lp, zp, L_crest, crest):
    """The line depth = a + b x L through the laser's pixels (L the satellite's colour, z the laser's depth below mean)
    and the measured crest (a fifth of their weight), fitted the other way round: the colour from the depth, then
    turned over. The colour is the noisy one (weed, the water's colour, haze), and a line fitted from a noisy variable
    comes out too flat: at Rohuneeme depth-from-colour left the flats 0.2 m too deep and 1-2 m water 0.4-0.7 m too
    shallow, colour-from-depth 0.0 to +0.2 from 0.5 to 2 m. Robust: twice more without the worst fifth (a boulder, a
    stray photon). (a, b, the depths' median absolute miss) or None when the light falls off the wrong way."""
    Lx, zx = np.append(Lp, [L_crest] * max(1, len(Lp) // 5)), np.append(zp, [crest] * max(1, len(Lp) // 5))
    keep = np.ones(len(Lx), bool)
    for _ in range(3):
        g, c = np.polyfit(zx[keep], Lx[keep], 1)  # (L = c + g z)
        res = np.abs(Lx - (c + g * zx))
        keep = res <= max(float(np.percentile(res[keep], 80)), 1e-3)
    # (The light's fall-off, 2K = -g per metre, 0.05-5: Tarifa's laser pixels at 0.3-2 m hardly changed in colour, g -0.003,
    # a line 124 m off; Berzciems's 2.4. Without one the chart's line is used.)
    if not -5.0 <= g <= -0.05:
        return None
    b, a = 1 / g, -c / g
    return float(a), float(b), float(np.median(np.abs(zp - (a + b * Lp))))


def survey(name, a, data, out, crest=CREST, red=True, weed=None):
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
    # (Scenes without whitecaps out at sea only: breaking waves' white water reads as dry sand in the near infrared. Judged
    # far out, not near the shore: there a dry bank is bright in the near infrared too, and the low-water scenes, where
    # Berzciems's bank is dry, were all thrown out as foamy.)
    still = np.array([np.nanmedian(x[off]) for x in NI]) < 0.015

    def colour(k):
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.nanmedian(np.log(np.clip(G[k] - gdeep[k, None, None], 1e-4, None)), 0)

    # The colour from each pixel's bright times (the owner, 23.11.2026: "adopt the bright-time colour"): its BRIGHT
    # percentile over the still scenes of ln(green - deep water's green) against the scene's own bright sand (the band's
    # brightest 5 %: the sun, haze and the water's clarity that day cancel). A summer bottom of weed or algae reads as
    # depth (Berzciems's lagoon 0.43-0.55 of the sand's brightness June-October, 0.8 in spring), and calm scenes are
    # mostly summer ones; the bright times see the bottom clean. Against the laser: Lielupe's line 0.67 -> 0.32 m,
    # Berzciems's check +-0.30 -> +-0.23, Puunsi's +-0.35 -> +-0.32. The calm median with under 12 still scenes.
    L0 = colour(still if still.sum() >= 12 else calm)
    sand = sea & (dsea > 20) & (dsea < 1500) & np.isfinite(L0)
    sand &= L0 >= np.nanpercentile(L0[sand], 95) if sand.any() else sand

    def bright(k):
        import warnings
        with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            A = np.log(np.clip(G[k] - gdeep[k, None, None], 1e-4, None))
            r = np.array([np.nanmedian(a[sand]) for a in A])
            return np.nanpercentile(A - r[:, None, None], BRIGHT, axis=0) + float(np.nanmedian(r))

    tint = np.nonzero(still)[0] if still.sum() >= 12 and sand.any() else None
    L = bright(tint) if tint is not None else colour(calm)
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
    doubts, notes = [], []  # (notes: what was done, not a doubt, on the sheet)
    # The laser's points here (laser_points: SlideRule, ~1 min), once: they choose the colour below and set the line.
    lp = laser_pixels(laser_points(LAT, LON), meta)
    # Which colour, the bright times or the calm median: the one the laser fits better (the owner, 23.11.2026: "Yes, fix
    # it"). The bright times, chosen on Baltic spots, broke on Tarifa's clear water (its fit error 124 m, the halves 22 m
    # apart; the calm median's line held there the day before). When the laser can't tell (too few pixels, or neither
    # line plausible, laser_fit): the bright times on a coast without a tide (the Baltic, where they were tested), the calm
    # median on a tidal one. shortcut: a halves test could choose there if a spot goes wrong that way.
    if tint is not None:
        Lc, errs = colour(calm), [np.inf, np.inf]
        if lp is not None:
            for i, Lx in enumerate((L, Lc)):
                s = dom[lp["rows"], lp["cols"]] & np.isfinite(Lx[lp["rows"], lp["cols"]]) & (lp["z"] >= 0.3) & (lp["z"] <= 2.0)
                top = dom & (dland > 30) & np.isfinite(Lx)
                f = laser_fit(Lx[lp["rows"], lp["cols"]][s], lp["z"][s], float(np.percentile(Lx[top], 98)), crest) \
                    if s.sum() >= LASER_MIN and top.any() else None
                errs[i] = f[2] if f is not None else np.inf
        calm_wins = errs[1] < errs[0] if np.isfinite(min(errs)) else tide.reach(lat, lon) >= TIDAL
        if calm_wins:
            L, tint = Lc, None
            dom = water & seaw & (dland > 20) & (dland <= 1200) & ~plants & ~river & np.isfinite(L)
        notes.append(f"colour: {'calm median' if tint is None else 'bright times'} (" + (
            f"the laser's fit error {errs[0]:.2f} m bright, {errs[1]:.2f} m calm)" if np.isfinite(min(errs))
            else "the laser couldn't tell: " + ("a tidal coast)" if calm_wins else "no tide)")))
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
    # (Off a tidal coast the waterline's scenes without ice or haze only: in a scene where over 5 % of the chart's 2 m+
    # water reads dry the sea isn't water's colour (Puunsi: 7 scenes of sea ice in March-April at 75-90 %, haze in October
    # 12-24 %; clear scenes 0-4 %). Spring is the Baltic's low water too, so the ice sat at the levels' low end and the
    # waterline put 6,200 ha of open water at 0.44 m (22.11.2026). A tidal coast's deep water may dry at low tide.)
    deep = dom & (gd > 2.0)
    if lv and tide.reach(lat, lon) < TIDAL and deep.sum() >= 200:
        sea_dry = [float(wet_dry(G[i], NI[i])[1][deep & np.isfinite(G[i])].mean()) if (deep & np.isfinite(G[i])).any() else 0.0
                   for i in range(len(days))]
        iced = {d for d, f in zip(days, sea_dry) if f > 0.05}
        if iced:
            lv = {d: v for d, v in lv.items() if d not in iced}
            notes.append(f"{len(iced)} scenes left out of the waterline: ice or haze over deep water")
    off60 = dom & (dland > 60)
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
    # The laser's true depths where it crossed (ICESat-2, laser_points): with LASER_MIN pixels in the satellite's own
    # 0.3-2 m it sets the line instead of our chart grid's 1.5-3 m (the ~100 m tiles that drew Puunsi's flats 2-4 m);
    # always the check: our depth minus the laser's, on the sheet (the owner, 22.11.2026: "Anchor on the laser").
    laser = {"points": 0}
    if lp is not None:
        sel = dom[lp["rows"], lp["cols"]] & np.isfinite(L[lp["rows"], lp["cols"]]) & (lp["z"] >= 0.3) & (lp["z"] <= 2.0)
        Lp, zp = L[lp["rows"], lp["cols"]][sel], lp["z"][sel]
        laser = {"points": lp["points"], "passes": lp["passes"], "msl": round(lp["msl"], 2), "pixels": int(sel.sum()),
                 "used": False, "old": round(float(np.median((a0 + b * Lp) - zp)), 2) if sel.any() else None}
        fit = laser_fit(Lp, zp, L_crest, crest) if sel.sum() >= LASER_MIN else None
        if fit is not None:
            a0, b, laser["fit_error"] = fit[0], fit[1], round(fit[2], 2)
            laser["used"] = True
        print(name, "laser:", laser, flush=True)
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
    band = dom & (dland <= 150) & (Z > 1.5)
    band_ha = float(band.sum() / 100)
    seen = not red_on and band_ha >= 1 and (dom & (dland > 150) & (Z < 0.5)).sum() >= 100
    # Taken as stones and weed, not a trough: its readings out, the bed fills it from the sand round it (area.py _bed_fill)
    # and the map counts it measured only near them. By default where the band is seen (the owner, 22.11.2026: "Yes, weed
    # by default": a trough drawn shallow is the safer mistake than stones drawn waist-deep); bars.json "weed": false keeps
    # a known trough, true drops the band anyway (Pirita and Russalka: "Stones and weed").
    weed_ha = 0.0
    if weed or (weed is None and seen):
        weed_ha, dom = band_ha, dom & ~band
    # B: the bed corrected round the laser's tracks (laser_correction), and the check against it where the satellite
    # sees (0.3-2 m): [median of ours minus the laser's, the spread round it (median absolute), pixels], before and
    # after. The after from passes the correction didn't use: each half of the passes corrects, the other half checks.
    # Off by over 0.3 m after: a doubt.
    if lp is not None:
        rr, cc, hv = lp["rows"], lp["cols"], lp["half"]
        good = dom[rr, cc] & np.isfinite(Z[rr, cc])
        # (Only where the satellite sees, 0.3-2 m: past it the colour stops changing and reads too shallow, and those
        # misses spread into the shallower water beside them: with 3 m the check on the other passes went 0.2-0.25 m
        # too deep at Kapteinu osta, Svetciems and Salacgriva. Deeper water is the chart's on the map anyway.)
        use = good & (lp["z"] >= 0.3) & (lp["z"] <= 2.0)
        chk = good & (lp["z"] >= 0.3) & (lp["z"] <= 2.0)

        def stat(dz):
            mid = float(np.median(dz))
            return [round(mid, 2), round(float(np.median(np.abs(dz - mid))), 2), int(len(dz))]

        if chk.sum() >= 10:
            laser["before"] = stat(Z[rr, cc][chk] - lp["z"][chk])
            held = []
            for h in (0, 1):
                fix = laser_correction(Z.shape, rr[use & (hv == h)], cc[use & (hv == h)],
                                       (lp["z"] - Z[rr, cc])[use & (hv == h)])
                t = chk & (hv != h)
                held += list((Z + fix)[rr[t], cc[t]] - lp["z"][t])
            if len(held) >= 10:
                laser["check"] = stat(np.array(held))
                # (The light's fall-off range was a guard for the chart's line; the laser measures the depth itself, so
                # within 0.3 m of it the line's slope is no doubt: Berzciems 2.37/m, +0.01 m from the laser.)
                if laser.get("used") and abs(laser["check"][0]) <= 0.3:
                    doubts[:] = [d for d in doubts if not d.startswith("light's fall-off")]
                if abs(laser["check"][0]) > 0.3:
                    doubts.append(f"{laser['check'][0]:+.2f} m from the laser's depths after its correction "
                                  f"({laser['check'][2]} pixels, 0.3-2 m)")
        if use.any():
            fix = laser_correction(Z.shape, rr[use], cc[use], (lp["z"] - Z[rr, cc])[use])
            Z = np.where(dom, Z + fix, Z)
            laser["corrected_ha"] = round(float(((np.abs(fix) > 0.05) & dom).sum() / 100), 1)
    # The scenes' two halves (every other scene the colour came from), on the one fit: the bed they agree on.
    if tint is not None:
        Z1, Z2 = a0 + b * bright(tint[0::2]), a0 + b * bright(tint[1::2])
    else:
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
    # The flats' heights by the waterline method (flat_heights) on every coast whose scenes' levels are on the map's zero
    # (the tide, or Copernicus in the Baltic; not a gauge's own zero): every pixel joined to the sea that is wet in 5-95 %
    # of the scenes, its measured height instead of its colour's depth (the colour of wet sand at low tide, or of a dark
    # lagoon's mud, says nothing of how high it stands). The Baltic's wind does what the tide does: Berzciems's levels
    # -0.47..+0.31 m, its lagoon behind the bank 0.2-0.4 m deep where the colour read its dark bottom as 0.8-1.2 (the
    # owner, 22.11.2026: "Its should be 30cm at 0m deep", then "Yes, build it").
    flat, flats_ha, reach = np.zeros_like(dom), 0.0, tide.reach(lat, lon)
    if lv and reach < TIDAL:
        reach = float(np.max(np.abs(list(lv.values()))))
    if lv and not (gauge and "the gauge's zero" in gauge):
        lab2, _ = label(wfrac >= 0.05)
        joined2 = np.unique(lab2[sea])
        cand = np.isin(lab2, joined2[joined2 > 0]) & (wfrac >= 0.05) & (wfrac <= 0.99)
        use_s = still if still.sum() >= 12 else np.ones(len(days), bool)
        hts = flat_heights(G[use_s], NI[use_s], [d for d, s in zip(days, use_s) if s], lv, cand, reach)
        flat = np.isfinite(hts)
        Z = np.where(flat, -hts, Z)
        lows = [lv[d] for d, s in zip(days, use_s) if s and d in lv]
        Z, gaps = flat_gaps(Z, flat, dom & ~flat, -min(lows) if lows else 0.0)
        flats_ha = float(flat.sum() / 100)
        if gaps:
            notes.append(f"{gaps / 100:.1f} ha never dry among the measured flats held at most {-min(lows):.2f} m or their neighbours'")
        print(name, f"flats: {flats_ha:.0f} ha measured by the waterline, heights "
              f"{np.nanpercentile(hts, 5) if flat.any() else 0:+.2f}..{np.nanpercentile(hts, 95) if flat.any() else 0:+.2f} m", flush=True)

    stat = {lev: float(((Z <= lev) & (dom | flat)).sum()) / 100 for lev in LEVELS}
    if two_k < 0.4 and not how.startswith("measured"):
        doubts.append(f"a weak fit (2K {two_k:.2f}/m, the crest given): bars further out may be missing")
    # (Drying judged off the measured flats: on a tidal coast they dry by nature.)
    dry, under1 = float(((Z < 0) & dom & ~flat).sum()) / 100, float(((Z <= 1.0) & dom & ~flat).sum()) / 100
    if under1 > 0 and dry / under1 > 0.3:
        doubts.append(f"{dry:.0f} of the {under1:.0f} ha under 1 m read as drying")
    # Too few calm scenes for the colour (murky, windy water: the Stor's mouth on the Elbe, 1 of 74) but flats measured
    # by the waterline, which needs no calm: the flats alone, the colour's depths out (the chart's there), and that no
    # doubt: its 1,396 ha of flats were held with the colour before.
    if calm.sum() < MIN_CALM and flat.sum() >= 2000:
        dom = np.zeros_like(dom)
        doubts[:] = [d for d in doubts if not d.startswith("only ") and not d.startswith("a weak fit")
                     and not d.startswith("light's fall-off") and "from the laser" not in d]
        notes.append(f"murky water ({int(calm.sum())} calm scenes): the tidal flats only, the colour's depths out")
    months = seasonal(G, NI, days, sea, flat, lv=lv, tide_reach=reach)
    if months is not None:
        notes.append(f"{np.isfinite(months[0]).sum() / 100:.1f} ha of seasonal water (wet by the month, not the tide)")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}-months.bin.gz").unlink(missing_ok=True)
    (out / f"{name}-months.json").unlink(missing_ok=True)
    pixels = _bed_out(name, a, Z, dom | flat, meta, out, months)
    rgb = np.nanmedian(RB[calm], 0) if np.isfinite(RB[calm]).any() else None
    res = {"area": name, "made": time.strftime("%Y-%m-%d"), "scenes": len(G), "calm": int(calm.sum()),
           "fit": {"a": round(a0, 3), "b": round(b, 3), "2K": round(two_k, 2), "crest": [round(L_crest, 3), round(crest, 2), how],
                   "water": [round(L_w, 3), round(D_w, 2), int(ok.sum())], "halves": round(split, 2),
                   "colour": "bright" if tint is not None else "calm"},
           "ha": {f"{k:g}": round(v, 1) for k, v in stat.items()}, "dark_ha": round(dark_ha, 1), "red_ha": round(red_ha, 1), "weed_ha": round(weed_ha, 1), "flats_ha": round(flats_ha, 1), "laser": laser, "notes": notes, "doubts": doubts,
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
    S, gap, foot = 600, 12, 150  # (600 px a panel: 15 m a pixel over the 9 km window, the 500 m bar 33 px)
    img = Image.new("RGB", (len(panels) * (S + gap) - gap, S + foot), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    for i, pnl in enumerate(panels):
        x = i * (S + gap)
        img.paste(pnl.resize((S, S), Image.BOX), (x, 0))
        dr.line([(x + 12, S - 14), (x + 12 + S * 500 // (N * 10), S - 14)], fill=(0, 0, 0), width=4)
        dr.text((x + 12, S - 30), "500 m", fill=(0, 0, 0))
    f = res["fit"]
    lines = [f"{res['area']}  {res['made']}  {res['calm']} calm of {res['scenes']} scenes  (left: true colour, right: depth)",
             f"crest {f['crest'][1]} m below mean ({f['crest'][2]}); water anchor {f['water'][1]} m; 2K {f['2K']}/m; "
             f"halves {f['halves']} m",
             "ha shallower than: " + ", ".join(f"{k} m {v}" for k, v in res["ha"].items()) +
             f"; dark bottoms: {res.get('red_ha', 0)} ha by red, {res.get('dark_ha', 0)} ha evened (tinted magenta), "
             f"{res.get('weed_ha', 0)} ha at the beach left out as weed; {res.get('flats_ha', 0)} ha of tidal flats "
             f"measured by the waterline",
             laser_text(res.get("laser")),
             "notes: " + ("; ".join(res.get("notes", [])) or "none"),
             "doubts: " + ("; ".join(res["doubts"]) or "none")]
    for k, s in enumerate(lines):
        dr.text((10, S + 10 + 22 * k), s, fill=(0, 0, 0))
    img.save(path)


def laser_text(lz):
    """The laser's line on the sheet and in the summary: what it had, whether the fit is on it, ours minus it."""
    if not lz or not lz.get("points"):
        return "laser: no ICESat-2 seabed points here"
    s = f"laser: {lz['points']} points, {lz['passes']} passes, {lz['pixels']} pixels at 0.3-2 m; line on " + \
        (f"the laser (fit +-{lz['fit_error']} m)" if lz.get("used") else "the chart (too few)")
    if lz.get("before"):
        s += f"; ours minus laser {lz['before'][0]:+.2f} m +-{lz['before'][1]} ({lz['before'][2]} px)"
    if lz.get("check"):
        s += f", corrected round its tracks {lz['check'][0]:+.2f} +-{lz['check'][1]} (checked on the other passes; " \
             f"{lz.get('corrected_ha', 0)} ha changed)"
    if lz.get("old") is not None:
        s += f"; the chart's line was {lz['old']:+.2f}"
    return s


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
                         r["fit"]["crest"][2].split(":")[0], r["ha"].get("0.3", 0), r.get("red_ha", 0), r.get("dark_ha", 0), r.get("weed_ha", 0),
                         laser_text(r.get("laser"))[7:], "; ".join(r["doubts"]) or "-"))
    for f in sorted(d.glob("*.failed")):
        rows.append((0, f.stem, "-", "-", "-", "-", "-", "-", "-", "-", "failed: " + " ".join(f.read_text().split())[-300:]))
    rows.sort()
    nl = chr(10)
    head = "| spot | calm/scenes | crest m | crest from | ha under 0.3 m | red ha | evened ha | weed ha | laser | doubts |" + nl + \
        "|---|---|---|---|---|---|---|---|---|---|" + nl
    (d / "SUMMARY.md").write_text(f"# Survey {time.strftime('%Y-%m-%d')}: {len(rows)} spots, "
                                  f"{sum(r[0] == 0 for r in rows)} failed, {sum(r[0] == 1 for r in rows)} doubtful" +
                                  nl + nl + head + "".join("| " + " | ".join(str(x) for x in r[1:]) + " |" + nl for r in rows))
    print((d / "SUMMARY.md").read_text())


def A_middle(box):
    """The box's middle (lat, lon): the area's first spot."""
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2)))))
    return lat, (box[0] + box[2] / 2) * 360 - 180


def apply(name, out, force=False, keep_bars=False, months_only=False):
    """The survey's bed into beds/ and bars.json "bed": it replaces the rule's bars (crest depths by rule) and the
    survey's own earlier contour shapes there; the owner's own shapes (lagoons...) kept; the river not opened (the
    owner's call)."""
    import shutil

    res = json.loads((Path(out) / f"{name}.json").read_text())
    p = HERE / "bars.json"
    (HERE / "beds").mkdir(exist_ok=True)

    def months_in(e):
        # (Its seasonal water's months with their own grid, seasonal(): beds/NAME-months.*, bars.json "months"; or none.)
        for ext in ("bin.gz", "json"):
            if (Path(out) / f"{name}-months.{ext}").exists():
                shutil.copy(Path(out) / f"{name}-months.{ext}", HERE / "beds" / f"{name}-months.{ext}")
            else:
                (HERE / "beds" / f"{name}-months.{ext}").unlink(missing_ok=True)
        # (Drawn on the map only for a spot that passed a look ("shore": true): 10 m pixels of water that moves drew blotches
        # and a dark smudge at Los Lances, the owner, 23.11.2026: "this is completely not looking good", then "Off, then
        # try B".)
        if (HERE / "beds" / f"{name}-months.json").exists() and e.get("shore"):
            e["months"] = name
        else:
            e.pop("months", None)

    if months_only:
        # (Only the months, the bed as it was: a held survey's months stand on the scenes' wet and dry, not its colour.)
        bars = json.loads(p.read_bytes().decode("utf-8"))
        months_in(bars.setdefault(name, {}))
        p.write_bytes((json.dumps(bars, indent=1) + "\n").encode("utf-8"))
        print(name, "months:", bars[name].get("months"), "; its bed kept:", bars[name].get("bed"))
        return
    if res["doubts"] and not force:
        raise SystemExit(f"{name}: doubtful, for the owner: {res['doubts']}")
    for ext in ("json", "bin.gz"):
        shutil.copy(Path(out) / f"{name}-bed.{ext}", HERE / "beds" / f"{name}.{ext}")
    bars = json.loads(p.read_bytes().decode("utf-8"))
    e = bars.setdefault(name, {})
    months_in(e)
    if not keep_bars:  # (dropped by default again since 22.11.2026, the owner: "make it according our automatic approach")
        e.pop("bars", None)
        e.pop("clear", None)  # (the circles only cut the rule's bars)
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
        apply(name, out, "--force" in args, "--keep-bars" in args, "--months" in args)
    else:
        import tables as T

        a = next((x for x in (T._json(T.STORE + "index.json") or {}).get("areas", []) if x["area"] == name), None)
        if a is None:
            raise SystemExit(f"{name}: not in the index")
        survey(name, a, opt("--data", str(Path.home() / "swan" / "sat")), out, float(opt("--crest", (T.bars_for(name) or {}).get("crest", CREST))),
               bool((T.bars_for(name) or {}).get("red", True)), (T.bars_for(name) or {}).get("weed"))
