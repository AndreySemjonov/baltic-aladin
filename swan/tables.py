"""SWAN's transfer tables, area by area (the owner, 19.11.2026: "Yes, build tables", then for the rest of the spots on
GitHub: "Yes, build it"; WindguruStationWatch docs/backlog/design.md "The waves engine", tools/webmap/swan.js reads them).

An area: a square ~8.7 km round a spot (256 x 256 cells of ~34 m; area.py: our depths, coast and breakwaters as the
web map's coarse fronts grid). SWAN (41.51, stationary) runs once for:
  - the open sea's waves (w runs): from each of 16 ways x 4/6/9 s x 0.5/2/3.5 m on every edge, no wind, no whitecapping
    (our wind keeps a sea up; without it SWAN wore a steep one down), with depth breaking, bottom friction, triads and
    diffraction (from the land's ways they reach nothing and take seconds);
  - our wind's own chop (c runs): from 16 ways x 5/10/15 m/s, no waves coming in, full growth;
both at a few water levels (levels_for; the owner, 19.11.2026: "we have to take into account the water level as well,
because the break of the waves will change"): mean sea level's runs in <area>/, the others' in <area>/lp0.6/, lm0.5/...
One job a level (an area's 144 runs ~50 min on GitHub). The map reads the hour's level (station-history /sea) and blends.
Each run packed: six W x H byte grids, rows from the north: the height (w: the share of the open sea's x 127.5; c:
metres x 63.75), the peak period (s x 12.75), the way it comes from (degrees x 256 / 360), the directional spread (degrees x
2), the surf's and the whitecaps' energy loss (SWAN's Ssurf, Swcap; w: over the open sea's height squared) as 10^(b/32 - 8),
0 none; land 0. (Three grids before 25.11.2026: the readers take the first three of either.) The map reads the
hour's open sea at the area's offshore point (its deepest cell) and blends the runs round it.

  python3 swan/tables.py plan [--only id,id] [--max N]   what to make: JSON [{area, lat, lon, spots, box, level}] (stdout):
                                                         new spots' areas at every level, the index's areas' missing levels
  python3 swan/tables.py make 'AREA_JSON' OUT            OUT/<area>/: area.json and out/<run>.bin.gz, out/reeds.bin.gz
                                                         (the reed cells, W x H bytes, 1 reeds) and out/reeds.json (their
                                                         outlines for the map; SWAN_EXE: swan.exe)
  python3 swan/tables.py publish OUT/<area>...           their runs up (INGEST_TOKEN)
  python3 swan/tables.py index OUT/<area>...             the index of areas: the store's own with these in, up
  python3 swan/tables.py index --drop area,area          the store's index without these areas (their files left), up
  python3 swan/tables.py depths [area...]                the index's areas' (or these) depth grids up, no SWAN: AREA/
                                                         depth.bin.gz (depth_bytes), the index's "depth" their stamp

Speed (19.11.2026): an open sea run 3-7 s, a wind run 10-60 s on 6 of the Mac mini's efficiency cores.
"""
import gzip
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import area as A  # noqa: E402

WAYS = [i * 22.5 for i in range(16)]
PERIODS = [4, 6, 9]
# (3.5 m since 25.11.2026: a storm's 2.5 m read 2 m's breaking, 17-20 % high on Pavilosta's bars; reference.py.)
HEIGHTS = [0.5, 2.0, 3.5]
WINDS = [5, 10, 15]
SIDE_M, CELLS = 8700.0, 256
STORE = "https://files.meteogust.com/tiles/swan/v1/"
UPLOAD = "https://data.meteogust.com/tiles/upload/swan/v1/"
CATALOG = "https://data.meteogust.com/spots/catalog"
SCALES = {"share": 127.5, "metres": 63.75, "period": 12.75, "way": 256 / 360, "spread": 2, "diss": 32, "dissBase": -8}
# SWAN's fields a run keeps (the owner, 25.11.2026: "make now B": the map draws what SWAN says, not rules on top): the
# height, peak period and way, and since then the directional spread (a confused sea offshore, lines near the shore), the
# surf's breaking and the whitecaps' energy loss (foam where waves break on a bar or the beach; spills scattered offshore).
FIELDS = "HSIGN TPS DIR DSPR DISSURF DISWCAP"
# The water levels (m over mean sea level) an area's runs are made at. The Gulf of Riga swings with the wind (a SW storm
# piles it up: +2.75 m at Parnu in 2005, +0.5 to +1 m on a windy day; an easterly empties it), the open coast less.
GULF_LEVELS = [-0.5, 0.0, 0.6, 1.2]
SEA_LEVELS = [-0.4, 0.0, 0.6]


def levels_for(lat, lon):
    """An area's levels: on a tidal coast five over the tide's reach and 0.2 m of surge, at least 0.6 m apart (tide.py:
    EOT20, as the map's level; Cape Town -1.3..+1.3, Cuxhaven -2.9..+2.9: the Baltic's -0.4..+0.6 had the far spots'
    waves at mid tide whatever the tide; the owner, 22.11.2026: "Also SWAN and take into account tides"); else the Gulf
    of Riga's (east of the Irbe strait) or the open Baltic's."""
    import tide

    reach = tide.reach(lat, lon)
    if reach >= tide.TIDAL:
        # (Three for a small tide, the Mediterranean's 0.3 m: -0.6, 0, 0.6; five for a 1 m one; past that at most 1 m
        # apart: a bank between two levels 2 m apart came out wrong; the Stor's seven, St-Malo's ~17; the owner,
        # 22.11.2026: "Big tides first".)
        span = reach + 0.2
        step = min(max(0.6, span / 2), 1.0)
        k = math.ceil(span / step - 1e-9)
        return [round(i * step, 1) for i in range(-k, k + 1)]
    return GULF_LEVELS if 56.9 <= lat <= 58.5 and 22.55 <= lon <= 24.7 else SEA_LEVELS


def level_dir(level):
    """A level's folder in an area's: none at mean sea level (the first tables'), else lp0.6/, lm0.5/."""
    return "" if level == 0 else f"l{'p' if level > 0 else 'm'}{abs(level):.1f}/"


def box_middle(box):
    """A box's (world 0-1) middle as (lat, lon)."""
    my = box[1] + box[3] / 2
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * my)))), (box[0] + box[2] / 2) * 360 - 180


def run_name(kind, way, a, b=None):
    return f"w-{way:05.1f}-{a:g}-{b:g}" if kind == "w" else f"c-{way:05.1f}-{a:g}"


def _json(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=A.UA), timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def plan(only=None, most=250):
    """The catalog's shown spots not in an area yet, grouped: a spot starts an area, the others within its middle 60 %
    join it (each area named by its first spot's id), at each of its levels; and the index's areas' levels not made yet
    (not with only). One job each (GitHub takes 256 a run)."""
    have = (_json(STORE + "index.json") or {}).get("areas", [])
    spots = [(s["id"], s["spot"]["lat"], s["spot"]["lon"]) for s in _json(CATALOG)["spots"] if not s.get("hidden")]
    if only == ["all"]:
        only = [s[0] for s in spots]
    if only:
        spots = [s for s in spots if s[0] in only]

    def inside(box, lat, lon, part):
        mx = (lon + 180) / 360
        my = (1 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / math.pi) / 2
        m = (1 - part) / 2
        return box[0] + box[2] * m <= mx <= box[0] + box[2] * (1 - m) and box[1] + box[3] * m <= my <= box[1] + box[3] * (1 - m)

    areas = []
    for sid, lat, lon in spots:
        if not only and any(inside(a["box"], lat, lon, 0.8) for a in have):
            continue
        if any(inside(a["box"], lat, lon, 0.6) for a in areas):
            next(a for a in areas if inside(a["box"], lat, lon, 0.6))["spots"].append(sid)
            continue
        name = re.sub(r"[^a-z0-9-]+", "-", sid.lower()).strip("-")[:40]
        areas.append({"area": name, "lat": lat, "lon": lon, "spots": [sid], "box": A.box_round(lat, lon, SIDE_M)})
    out = [{**{k: a[k] for k in ("area", "lat", "lon", "spots", "box")}, "level": lv} for a in areas for lv in levels_for(a["lat"], a["lon"])]
    if not only:
        for a in have:
            made = a.get("levels", [0.0])
            out += [{"area": a["area"], "spots": a.get("spots", []), "box": a["box"], "level": lv}
                    for lv in levels_for(*box_middle(a["box"])) if lv not in made]
    return out[:most]


# The reeds in SWAN as plants in shallow water, not land (the owner, 20.11.2026: "build B,C,D,E", E: reeds partly letting
# waves through at high water): common reed (Phragmites) ~2 m tall, 7 mm stems, ~120 a square metre, drag 1, on a 0.3 m
# bed (as the lagoons': dry below -0.3 m); SWAN's VEGETATION (Dalrymple's drag) with NPLANTS the density a cell.
REEDS = {"height": 2.0, "diameter": 0.007, "density": 120, "drag": 1.0, "bed": 0.3}


def bars_for(area):
    """An area's sandbars (bars.json, area.py _bars), None without."""
    return json.loads((Path(__file__).parent / "bars.json").read_text()).get(area)


# The map's depth bytes start this far below mean sea level (negative: above it), so a bank above it fits (index "depthBase").
DEPTH_BASE = -0.5
# Its bytes' step (index "depthStep"; without it the map reads 0.01): 2 cm reach ~4.5 m below mean sea level, the
# deeper bars too (the owner, 20.11.2026: "Yes, extend it"; 1 cm ended at ~2 m and Pavilosta's second bar at 2.2 m with it).
DEPTH_STEP = 0.02


# An area with a satellite bed (bars.json "bed", area.py _bed) gets the map's depth grid this many times finer than
# SWAN's: ~8.5 m on a 34 m grid, the bed's own 10 m (the owner, 20.11.2026: "Yes, do A"; index "depthW", "depthH").
FINE = 4


def depth_scale(lat, lon):
    """An area's depth bytes' base and step (the index's depthBase, depthStep; the map reads them per area): the
    Baltic's DEPTH_BASE and DEPTH_STEP; on a tidal coast from just over its highest tide (the reach + 0.5 m above mean
    sea level: flats that dry every low tide stand up to 3-4 m above it on a 6-8 m coast) to 4.5 m below its lowest, in
    as fine steps as 253 allow (Cape Town +1.6..-5.6 m in 2.8 cm; the owner, 22.11.2026: "Big tides first")."""
    import tide

    reach = tide.reach(lat, lon)
    if reach < tide.TIDAL:
        return DEPTH_BASE, DEPTH_STEP
    base = -round(reach + 0.5, 1)
    return base, round(max(DEPTH_STEP, (reach + 4.5 - base) / 253), 3)


def map_depth(area, box, W, H, depth):
    """The map's depth bytes (depth_bytes), their grid's size (SWAN's grid, or with a satellite bed built FINE x finer) and
    where the depth is known: the bed's share a cell as bytes (0-255, measured.bin.gz), "owner" where the owner gave the
    beach's shape (bars.json "owner": Liepaja DR's bars, Parnu's shelf), else None: the old depth tiles' only, which the
    map doesn't draw (the owner, 22.11.2026, Puunsi's flats drawn 2-4 m: "Yes, A + C"); and the seasonal water's months
    (map_months, months.bin.gz) or None."""
    bars = bars_for(area) or {}
    scale = depth_scale(*box_middle(box))
    # (A country's survey there too, area.surveys: its cells measured; the fine grid read once to know.)
    if bars.get("bed") or bars.get("months") or A.surveyed_box(box):
        # (An area with seasonal water but its survey's bed held, the old Tarifa areas: the fine grid for the months, nothing
        # measured on it.)
        cover = np.zeros((H * FINE, W * FINE), np.float32)
        fine = A.build(box, W * FINE, H * FINE, None, bars, cover)
        # (Nothing measured on our grid's land and reeds: the bed's cover spilled onto them, coded 0.2 m deep, and where the
        # base map has water there, a river past the opened mouth, the map drew it measured and dry at -0.5 m: the Lielupe
        # band at 57.01 N, 23.98 E, 23.11.2026.)
        cover[fine <= A.SEA_MIN] = 0
        meas = np.round(np.clip(cover, 0, 1) * 255).astype(np.uint8).tobytes() if bars.get("bed") or cover.any() \
            else "owner" if bars.get("owner") else None
        return depth_bytes(fine, *scale), W * FINE, H * FINE, meas, map_months(area, box, W, H, fine)
    return depth_bytes(depth, *scale), W, H, "owner" if bars.get("owner") else None, None


def map_months(area, box, W, H, fine):
    """The seasonal water's wet share by month on the map's fine grid (survey.py seasonal; the owner, 23.11.2026, Tarifa's
    Los Lances: "Yes, build B"): 12 planes of depthW x depthH bytes, the share x 254, 255 none, and a 13th, 254 where every
    scene saw plants (a marsh; "Yes, do A") (months.bin.gz, the index's
    "months"), only on cells our grid calls land or reeds: the sea's cells have their measured bed. None without."""
    name = (bars_for(area) or {}).get("months")
    if not name or not (A.BEDS / f"{name}-months.json").exists():
        return None
    f = A.BEDS / f"{name}-months.bin.gz"
    meta = json.loads((A.BEDS / f"{name}-months.json").read_bytes().decode())
    bw, bh, FW, FH = meta["W"], meta["H"], W * FINE, H * FINE
    m = np.frombuffer(gzip.decompress(f.read_bytes()), np.uint8)
    m = m.reshape(len(m) // (bh * bw), bh, bw)  # (12 months, and since the marsh a 13th: plants in every scene, 254)
    c0, r0 = round((meta["box"][0] - box[0]) / box[2] * FW), round((meta["box"][1] - box[1]) / box[3] * FH)
    out = np.full((len(m), FH, FW), 255, np.uint8)
    out[:, r0:r0 + bh, c0:c0 + bw] = m[:, :FH - r0, :FW - c0]
    out[:, fine > A.SEA_MIN] = 255
    # (Our shore on the map, the owner, 23.11.2026, "Ok with A": the grid's land where nothing else was seen, 64 in the
    # 13th plane, so the map tells our land from our water everywhere in the area; the sea's cells stay 255.)
    if len(out) >= 13:
        out[12][(fine <= A.SEA_MIN) & (out[12] == 255) & (out[:12] == 255).all(0)] = 64
    return out.tobytes() if (out < 255).any() else None


def measured_key(measured):
    """The index's "measured" for map_depth's: "bed" (its measured.bin.gz), "owner", or None (left out)."""
    return "bed" if isinstance(measured, bytes) else measured


def depth_bytes(depth, base=DEPTH_BASE, step=DEPTH_STEP):
    """The bed for the map's shallows (the owner, 20.11.2026: "show lagoon with sandbags on map too depending on water
    level", then "Yes, build A"): a byte a cell (W x H, rows from the north), DEPTH_STEPs below DEPTH_BASE (a sandbank's
    crest up to 0.5 m above mean sea level: Berzciems's, "Usually bank appears at 20-30cm water level") to 4.56 m below
    mean sea level, 254 deeper; land and reeds the nearest sea cell's (the map's own shore mask says where the water is,
    and the shore's cells don't dip). shortcut: only the shallows kept, a byte; deeper beds need 16 bits if the map wants them."""
    from scipy.ndimage import distance_transform_edt

    # Reeds as the reed lagoon's water (they stand in it), and land: its rim of 3 cells the nearest sea cell's (the map's
    # shoreline samples there: no dark rim), further in fading over 8 cells to the reed lagoon's depth. All the nearest
    # sea cell's before, its rays showed as fans and wedges where the map's coast has water ours calls land (the lagoon
    # behind Haademeeste's reed islands: the owner, 22.11.2026, "make it according our automatic approach").
    depth = np.where(depth == A.REED, A.LAGOON, depth)
    sea = depth > A.SEA_MIN  # (A.LAND land; a bank's bed above mean sea level negative)
    dist, (r, c) = distance_transform_edt(~sea, return_indices=True)
    f = np.clip((dist - 3) / 8, 0, 1)
    n = np.round((np.where(sea, depth, depth[r, c] * (1 - f) + A.LAGOON * f) - base) / step)
    return np.where(n > 253, 254, np.clip(n, 0, 253)).astype(np.uint8).tobytes()


def depths(names=None):
    """The index's areas' depth grids (or these areas'), built as make builds them, up; the index then stamps them."""
    key = _token()
    areas = (_json(STORE + "index.json") or {}).get("areas", [])
    todo = [a for a in areas if not names or a["area"] in names]
    if names and len(todo) != len(names):
        raise SystemExit(f"not in the index: {sorted(set(names) - {a['area'] for a in todo})}")
    stamp = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
    for a in todo:
        t0 = time.time()
        b, a["depthW"], a["depthH"], meas, months = map_depth(a["area"], a["box"], a["W"], a["H"],
                                                              A.build(a["box"], a["W"], a["H"], None, bars_for(a["area"])))
        _put(f"{a['area']}/depth.bin.gz", gzip.compress(b, 9), key)
        if isinstance(meas, bytes):
            _put(f"{a['area']}/measured.bin.gz", gzip.compress(meas, 9), key)
        if months:
            _put(f"{a['area']}/months.bin.gz", gzip.compress(months, 9), key)
        a["measured"], a["months"] = measured_key(meas), bool(months)
        print(a["area"], f"depth up in {time.time() - t0:.0f} s ({a['depthW']} x {a['depthH']})", flush=True)
    # (The index read again: a remake's index may have come meanwhile.)
    now = {a["area"]: a for a in (_json(STORE + "index.json") or {}).get("areas", [])}
    for a in todo:
        if a["area"] in now:
            now[a["area"]]["depth"] = stamp
            now[a["area"]]["depthBase"], now[a["area"]]["depthStep"] = depth_scale(*box_middle(a["box"]))
            # (The map's grid's size where finer than SWAN's: a satellite bed's.)
            now[a["area"]].pop("depthW", None), now[a["area"]].pop("depthH", None)
            if a["depthW"] != a["W"]:
                now[a["area"]]["depthW"], now[a["area"]]["depthH"] = a["depthW"], a["depthH"]
            now[a["area"]].pop("measured", None)
            if a["measured"]:
                now[a["area"]]["measured"] = a["measured"]
            now[a["area"]].pop("months", None)
            if a["months"]:
                now[a["area"]]["months"] = True
            # (Muddy water: the map says "muddy water: depth not seen" where nothing was measured; survey.py MUD_RED.)
            now[a["area"]].pop("muddy", None)
            if (bars_for(a["area"]) or {}).get("muddy"):
                now[a["area"]]["muddy"] = True
            # (Its bars for the map's lines: [[metres from the waterline, crest depth], ...].)
            if (bars_for(a["area"]) or {}).get("bars"):
                now[a["area"]]["bars"] = bars_for(a["area"])["bars"]
    print("index:", _put("index.json", json.dumps({"areas": list(now.values())}).encode(), key), len(todo), "depths stamped")


def swan_files(home, depth, box, W, H, level, block="HSIGN TPS DIR"):
    """An area's bed (and reeds) for SWAN in HOME (bottom.dat, plants.dat), and the INPUT's head and tail round a run's own
    lines (its waves at the edges, its wind): the tables' runs and the reference runs (reference.py) share them. A run's
    INPUT sits two folders down (HOME/runs/<run>/). BLOCK: the fields written to out.mat."""
    reeds = depth == A.REED
    lat = math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2))))
    dx = dy = box[2] * A.EARTH * math.cos(lat) / W
    bed = np.where(reeds, REEDS["bed"], depth)
    # (Land -999; a bank's bed above mean sea level as it is, negative: dry below its height, wet above.)
    (home / "bottom.dat").write_text("\n".join(" ".join("-999" if d <= A.SEA_MIN else f"{d:.2f}" for d in row) for row in bed) + "\n")
    if reeds.any():
        (home / "plants.dat").write_text("\n".join(" ".join(str(REEDS["density"]) if r else "0" for r in row) for row in reeds) + "\n")
    x0, y0, xl, yl = dx / 2, dy / 2, (W - 1) * dx, (H - 1) * dy
    head = f"""SET LEVEL {level:g} NAUTICAL
MODE STATIONARY TWODIMENSIONAL
COORDINATES CARTESIAN
CGRID REGULAR {x0:.1f} {y0:.1f} 0 {xl:.1f} {yl:.1f} {W - 1} {H - 1} CIRCLE 24 0.05 1.0 23
INPGRID BOTTOM REGULAR {x0:.1f} {y0:.1f} 0 {W - 1} {H - 1} {dx:.3f} {dy:.3f} EXCEPTION -999
READINP BOTTOM 1 '../../bottom.dat' 1 0 FREE
"""
    if reeds.any():
        head += f"""INPGRID NPLANTS REGULAR {x0:.1f} {y0:.1f} 0 {W - 1} {H - 1} {dx:.3f} {dy:.3f}
READINP NPLANTS 1 '../../plants.dat' 1 0 FREE
"""
    tail = (f"VEGETATION 1 {REEDS['height']} {REEDS['diameter']} 1 {REEDS['drag']}\n" if reeds.any() else "") + f"""BREAKING
FRICTION JONSWAP
TRIAD
DIFFRACTION
NUMERIC STOPC 0.005 0.01 0.005 98 STAT 30
BLOCK 'COMPGRID' NOHEADER 'out.mat' LAYOUT 1 {block}
COMPUTE
STOP
"""
    return head, tail


def make(spec, out):
    spec = json.loads(spec) if isinstance(spec, str) else spec
    home = Path(out) / spec["area"]
    (home / "runs").mkdir(parents=True, exist_ok=True)
    # (An area made before: its own box, so every level's runs lie on one grid.)
    box, W, H = spec.get("box") or A.box_round(spec["lat"], spec["lon"], SIDE_M), CELLS, CELLS
    level = float(spec.get("level", 0)); ldir = level_dir(level)
    t0 = time.time()
    outlines = []
    depth = A.build(box, W, H, outlines, bars_for(spec["area"]))
    lat = math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2))))
    dx = dy = box[2] * A.EARTH * math.cos(lat) / W
    sea = depth > A.SEA_MIN
    if sea.mean() < 0.02:
        print(spec["area"], "no sea in the box: skipped")
        return None
    # The offshore point: the deepest cell (the hour's open sea is read there).
    r, c = np.unravel_index(np.argmax(np.where(sea, depth, -1)), depth.shape)
    mx, my = box[0] + (c + 0.5) / W * box[2], box[1] + (r + 0.5) / H * box[3]
    off = [round(math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * my)))), 5), round(mx * 360 - 180, 5)]
    reeds = depth == A.REED
    if reeds.any():
        (home / "out").mkdir(exist_ok=True)
        (home / "out" / "reeds.bin.gz").write_bytes(gzip.compress(reeds.astype(np.uint8).tobytes(), 9))
        # (Their outlines for the map: {"p": [[ring, ...], ...]}, as the breakwaters' tiles.)
        (home / "out" / "reeds.json").write_text(json.dumps({"p": outlines}, separators=(",", ":")))
    if not ldir:
        (home / "out").mkdir(exist_ok=True)
        b, depthW, depthH, meas, months = map_depth(spec["area"], box, W, H, depth)
        (home / "out" / "depth.bin.gz").write_bytes(gzip.compress(b, 9))
        if isinstance(meas, bytes):
            (home / "out" / "measured.bin.gz").write_bytes(gzip.compress(meas, 9))
        if months:
            (home / "out" / "months.bin.gz").write_bytes(gzip.compress(months, 9))
    print(spec["area"], f"level {level:g} grid {time.time() - t0:.0f} s: reeds {reeds.sum()} cells, sea {sea.mean() * 100:.0f} %, deepest {depth.max():.0f} m at {off}", flush=True)
    head, tail = swan_files(home, depth, box, W, H, level, FIELDS)
    runs = []
    for way in WAYS:
        for T in PERIODS:
            for Hin in HEIGHTS:
                sides = "".join(f"BOUNDSPEC SIDE {s} CCW CONSTANT PAR {Hin} {T} {way} 20\n" for s in ("WEST", "NORTH", "EAST", "SOUTH"))
                runs.append((run_name("w", way, T, Hin), f"{head}BOUND SHAPESPEC JONSWAP 3.3 PEAK DSPR DEGREES\n{sides}GEN3\nOFF WCAPPING\nOFF QUADRUPL\n{tail}"))
    for way in WAYS:
        for U in WINDS:
            runs.append((run_name("c", way, U), f"{head}WIND {U} {way}\nGEN3\n{tail}"))
    exe, env = os.environ.get("SWAN_EXE", "swan.exe"), {**os.environ, "OMP_NUM_THREADS": str(os.cpu_count() or 2)}
    failed = 0
    for k, (name, body) in enumerate(runs):
        if (home / "out" / f"{ldir}{name}.bin.gz").exists():
            continue
        run = home / "runs" / name
        run.mkdir(exist_ok=True)
        (run / "INPUT").write_text(f"PROJECT 'table' '1' '{name}'\n" + body)
        t = time.time()
        subprocess.run([exe], cwd=run, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
        if (run / "out.mat").exists():
            pack(home, name, ldir)
        else:
            failed += 1
            print(name, "FAILED", flush=True)
        if k % 12 == 0:
            print(f"{spec['area']} {k + 1}/{len(runs)} {name} {time.time() - t:.0f} s", flush=True)
    meta = {"area": spec["area"], "spots": spec.get("spots", []), "box": box, "W": W, "H": H, "dx": dx, "dy": dy, "offshore": off,
            "ways": WAYS, "sea": [0, 337.5], "periods": PERIODS, "heights": HEIGHTS, "winds": WINDS, "scales": SCALES, "levels": [level],
            "reeds": int(reeds.sum()),
            "made": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}
    if not ldir:
        meta["depth"], (meta["depthBase"], meta["depthStep"]) = meta["made"], depth_scale(*box_middle(box))
        if depthW != W:
            meta["depthW"], meta["depthH"] = depthW, depthH
        if measured_key(meas):
            meta["measured"] = measured_key(meas)
        if months:
            meta["months"] = True
        if (bars_for(spec["area"]) or {}).get("bars"):
            meta["bars"] = bars_for(spec["area"])["bars"]
        # (A river mouth's: the map draws its waves on the base map's river water too.)
        if (bars_for(spec["area"]) or {}).get("river"):
            meta["river"] = True
        if (bars_for(spec["area"]) or {}).get("muddy"):
            meta["muddy"] = True
    (home / "area.json").write_text(json.dumps(meta))
    print(spec["area"], f"done in {(time.time() - t0) / 60:.0f} min, {failed} failed", flush=True)
    if failed > len(runs) // 10:
        raise SystemExit(f"{spec['area']}: {failed} runs failed")
    return home


def pack(home, name, ldir=""):
    """A finished run's out.mat as AREA/out/<level dir><run>.bin.gz; its big files removed."""
    from scipy.io import loadmat

    run = home / "runs" / name
    m = loadmat(run / "out.mat")
    pick = lambda prefix: next(v for k, v in m.items() if k.startswith(prefix))  # noqa: E731
    hs, tp, way, spr, surf, wcap = (np.nan_to_num(pick(p), nan=-1.0) for p in ("Hsig", "TPsmoo", "Dir", "Dspr", "Ssurf", "Swcap"))
    land = hs < 0
    norm = float(name.split("-")[3]) if name.startswith("w-") else None
    h = np.clip(np.round(hs / norm * 127.5 if norm else hs * 63.75), 0, 255)
    t = np.clip(np.round(tp * 12.75), 0, 255)
    d = np.round(np.mod(way, 360) * 256 / 360) % 256
    sp = np.clip(np.round(spr * SCALES["spread"]), 0, 255)
    diss = [np.where(x > 0, np.clip(np.round((np.log10(np.maximum(x / (norm * norm if norm else 1), 1e-12)) - SCALES["dissBase"]) * SCALES["diss"]), 1, 255), 0)
            for x in (surf, wcap)]
    for g in (h, t, d, sp, *diss):
        g[land] = 0
    (home / "out" / ldir).mkdir(parents=True, exist_ok=True)
    (home / "out" / f"{ldir}{name}.bin.gz").write_bytes(gzip.compress(np.stack([h, t, d, sp, *diss]).astype(np.uint8).tobytes(), 9))
    for f in run.iterdir():
        f.unlink()
    run.rmdir()


def _token():
    t = os.environ.get("INGEST_TOKEN")
    env = Path.home() / ".meteogust.env"
    if not t and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("INGEST_TOKEN="):
                t = line.split("=", 1)[1].strip().strip("'\"")
    if not t:
        raise SystemExit("INGEST_TOKEN is not set")
    return t


def _put(path, data, key):
    for attempt in range(4):
        try:
            req = urllib.request.Request(UPLOAD + path, data=data, method="PUT", headers={"Authorization": "Bearer " + key, **A.UA})
            with urllib.request.urlopen(req, timeout=120) as reply:
                return reply.read().decode().strip()
        except (urllib.error.URLError, OSError) as error:
            if (isinstance(error, urllib.error.HTTPError) and error.code < 500) or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def publish(homes):
    key = _token()
    for home in map(Path, homes):
        name = json.loads((home / "area.json").read_text())["area"]
        runs = sorted([*(home / "out").rglob("*.bin.gz"), *(home / "out").glob("reeds.json")])
        for run in runs:
            _put(f"{name}/{run.relative_to(home / 'out').as_posix()}", run.read_bytes(), key)
        print(name, len(runs), "runs up", flush=True)


def index(homes, drop=()):
    """The store's index with these areas in (a made area replacing its old self, its levels added to the old's), up
    last: the map never lists an area or a level whose runs aren't there yet. Areas in drop left out (the owner,
    20.11.2026: "remove the old liepaja area from the index", the Mac pilot's beside liepaja-dr and liepaja-mols)."""
    key = _token()
    areas = {a["area"]: a for a in (_json(STORE + "index.json") or {}).get("areas", [])}
    for name in drop:
        if areas.pop(name, None) is None:
            raise SystemExit(f"{name}: not in the index")
    for home in map(Path, homes):
        meta = json.loads((home / "area.json").read_text())
        old = areas.get(meta["area"])
        meta["levels"] = sorted(set(old.get("levels", [0.0]) if old else []) | set(meta.get("levels", [0.0])))
        # (Its depth grid's stamp kept when only other levels were made.)
        if "depth" not in meta and old and "depth" in old:
            meta["depth"] = old["depth"]
            for k in ("depthBase", "depthStep", "depthW", "depthH", "bars", "river", "measured", "months", "muddy"):
                if k in old:
                    meta[k] = old[k]
        areas[meta["area"]] = meta
    print("index:", _put("index.json", json.dumps({"areas": list(areas.values())}).encode(), key), len(areas), "areas")


if __name__ == "__main__":
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "plan":
        only = rest[rest.index("--only") + 1].split(",") if "--only" in rest and rest[rest.index("--only") + 1] else None
        most = int(rest[rest.index("--max") + 1]) if "--max" in rest else 250
        print(json.dumps(plan(only, most)))
    elif cmd == "make":
        make(rest[0], rest[1])
    elif cmd == "publish":
        publish(rest)
    elif cmd == "depths":
        depths(rest or None)
    elif cmd == "index":
        if "--drop" in rest:
            i = rest.index("--drop")
            index(rest[:i] + rest[i + 2:], [a for a in rest[i + 1].split(",") if a])
        else:
            index(rest)
