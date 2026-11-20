"""SWAN's transfer tables, area by area (the owner, 19.11.2026: "Yes, build tables", then for the rest of the spots on
GitHub: "Yes, build it"; WindguruStationWatch docs/backlog/design.md "The waves engine", tools/webmap/swan.js reads them).

An area: a square ~8.7 km round a spot (256 x 256 cells of ~34 m; area.py: our depths, coast and breakwaters as the
web map's coarse fronts grid). SWAN (41.51, stationary) runs once for:
  - the open sea's waves (w runs): from each of 16 ways x 4/6/9 s x 0.5/2 m on every edge, no wind, no whitecapping
    (our wind keeps a sea up; without it SWAN wore a steep one down), with depth breaking, bottom friction, triads and
    diffraction (from the land's ways they reach nothing and take seconds);
  - our wind's own chop (c runs): from 16 ways x 5/10/15 m/s, no waves coming in, full growth;
both at a few water levels (levels_for; the owner, 19.11.2026: "we have to take into account the water level as well,
because the break of the waves will change"): mean sea level's runs in <area>/, the others' in <area>/lp0.6/, lm0.5/...
One job a level (an area's 144 runs ~50 min on GitHub). The map reads the hour's level (station-history /sea) and blends.
Each run packed: three W x H byte grids, rows from the north: the height (w: the share of the open sea's x 127.5; c:
metres x 63.75), the peak period (s x 12.75), the way it comes from (degrees x 256 / 360); land 0. The map reads the
hour's open sea at the area's offshore point (its deepest cell) and blends the runs round it.

  python3 swan/tables.py plan [--only id,id] [--max N]   what to make: JSON [{area, lat, lon, spots, box, level}] (stdout):
                                                         new spots' areas at every level, the index's areas' missing levels
  python3 swan/tables.py make 'AREA_JSON' OUT            OUT/<area>/: area.json and out/<run>.bin.gz, out/reeds.bin.gz
                                                         (the reed cells, W x H bytes, 1 reeds) and out/reeds.json (their
                                                         outlines for the map; SWAN_EXE: swan.exe)
  python3 swan/tables.py publish OUT/<area>...           their runs up (INGEST_TOKEN)
  python3 swan/tables.py index OUT/<area>...             the index of areas: the store's own with these in, up
  python3 swan/tables.py index --drop area,area          the store's index without these areas (their files left), up

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
HEIGHTS = [0.5, 2.0]
WINDS = [5, 10, 15]
SIDE_M, CELLS = 8700.0, 256
STORE = "https://files.meteogust.com/tiles/swan/v1/"
UPLOAD = "https://data.meteogust.com/tiles/upload/swan/v1/"
CATALOG = "https://data.meteogust.com/spots/catalog"
SCALES = {"share": 127.5, "metres": 63.75, "period": 12.75, "way": 256 / 360}
# The water levels (m over mean sea level) an area's runs are made at. The Gulf of Riga swings with the wind (a SW storm
# piles it up: +2.75 m at Parnu in 2005, +0.5 to +1 m on a windy day; an easterly empties it), the open coast less.
GULF_LEVELS = [-0.5, 0.0, 0.6, 1.2]
SEA_LEVELS = [-0.4, 0.0, 0.6]


def levels_for(lat, lon):
    """An area's levels: the Gulf of Riga's (east of the Irbe strait) or the open Baltic's.
    shortcut: two fixed sets for the Baltic; tidal coasts need theirs from the tide's range (the world's spots)."""
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


def make(spec, out):
    spec = json.loads(spec) if isinstance(spec, str) else spec
    home = Path(out) / spec["area"]
    (home / "runs").mkdir(parents=True, exist_ok=True)
    # (An area made before: its own box, so every level's runs lie on one grid.)
    box, W, H = spec.get("box") or A.box_round(spec["lat"], spec["lon"], SIDE_M), CELLS, CELLS
    level = float(spec.get("level", 0)); ldir = level_dir(level)
    t0 = time.time()
    outlines = []
    depth = A.build(box, W, H, outlines)
    lat = math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2))))
    dx = dy = box[2] * A.EARTH * math.cos(lat) / W
    sea = depth >= 0
    if sea.mean() < 0.02:
        print(spec["area"], "no sea in the box: skipped")
        return None
    # The offshore point: the deepest cell (the hour's open sea is read there).
    r, c = np.unravel_index(np.argmax(np.where(sea, depth, -1)), depth.shape)
    mx, my = box[0] + (c + 0.5) / W * box[2], box[1] + (r + 0.5) / H * box[3]
    off = [round(math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * my)))), 5), round(mx * 360 - 180, 5)]
    reeds = depth == -2
    if reeds.any():
        (home / "out").mkdir(exist_ok=True)
        (home / "out" / "reeds.bin.gz").write_bytes(gzip.compress(reeds.astype(np.uint8).tobytes(), 9))
        # (Their outlines for the map: {"p": [[ring, ...], ...]}, as the breakwaters' tiles.)
        (home / "out" / "reeds.json").write_text(json.dumps({"p": outlines}, separators=(",", ":")))
    print(spec["area"], f"level {level:g} grid {time.time() - t0:.0f} s: reeds {reeds.sum()} cells, sea {sea.mean() * 100:.0f} %, deepest {depth.max():.0f} m at {off}", flush=True)
    (home / "bottom.dat").write_text("\n".join(" ".join("-9" if d < 0 else f"{d:.2f}" for d in row) for row in depth) + "\n")
    x0, y0, xl, yl = dx / 2, dy / 2, (W - 1) * dx, (H - 1) * dy
    head = f"""SET LEVEL {level:g} NAUTICAL
MODE STATIONARY TWODIMENSIONAL
COORDINATES CARTESIAN
CGRID REGULAR {x0:.1f} {y0:.1f} 0 {xl:.1f} {yl:.1f} {W - 1} {H - 1} CIRCLE 24 0.05 1.0 23
INPGRID BOTTOM REGULAR {x0:.1f} {y0:.1f} 0 {W - 1} {H - 1} {dx:.3f} {dy:.3f} EXCEPTION -9
READINP BOTTOM 1 '../../bottom.dat' 1 0 FREE
"""
    tail = """BREAKING
FRICTION JONSWAP
TRIAD
DIFFRACTION
NUMERIC STOPC 0.005 0.01 0.005 98 STAT 30
BLOCK 'COMPGRID' NOHEADER 'out.mat' LAYOUT 1 HSIGN TPS DIR
COMPUTE
STOP
"""
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
    hs, tp, way = (np.nan_to_num(pick(p), nan=-1.0) for p in ("Hsig", "TPsmoo", "Dir"))
    land = hs < 0
    h = np.clip(np.round(hs / float(name.split("-")[3]) * 127.5 if name.startswith("w-") else hs * 63.75), 0, 255)
    t = np.clip(np.round(tp * 12.75), 0, 255)
    d = np.round(np.mod(way, 360) * 256 / 360) % 256
    for g in (h, t, d):
        g[land] = 0
    (home / "out" / ldir).mkdir(parents=True, exist_ok=True)
    (home / "out" / f"{ldir}{name}.bin.gz").write_bytes(gzip.compress(np.stack([h, t, d]).astype(np.uint8).tobytes(), 9))
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
    elif cmd == "index":
        if "--drop" in rest:
            i = rest.index("--drop")
            index(rest[:i] + rest[i + 2:], [a for a in rest[i + 1].split(",") if a])
        else:
            index(rest)
