"""SWAN's reference runs: the open sea's waves and our wind in ONE run, against the tables' two runs glued as the map glues
them (the owner, 25.11.2026: "make now B, starting with the reference runs"; WindguruStationWatch docs/backlog/design.md
"One wave engine from SWAN's physics"). The tables run the sea's waves without wind and our wind without waves, and
swan.js swanNow adds them up; a real sea is one: the bank breaks the sum, the wind grows on what is left. These cases are
the checks the tables' new glue and fields must pass, and they say whether an area needs combined tables (option C).

  python swan/reference.py prep OUT [case,case]   OUT/<case>/: bottom.dat, case.json, depth.npy, runs/{w,c,b}/INPUT
                                                  (w: the sea alone, as the tables; c: our wind alone, as the tables;
                                                  b: both, full physics), SWAN's extra fields in out.mat
  python swan/reference.py report OUT [case...]   each case's numbers: by depth band, and along a line from 4 km
                                                  up-wave to the spot's pin

SWAN runs where it is (the Mac mini: ~/swan/swan4151/swan.exe in each runs/<run>/ folder).
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import area as A  # noqa: E402
import tables as T  # noqa: E402

# The cases: the owner's days and the places that disagree (25.11.2026). sea: the open sea's waves (Hs m, peak s, from);
# wind: our wind (m/s, from).
CASES = {
    # The owner's Pavilosta screenshot (25.11.2026): "why waves not coming till the beach".
    "pav-storm": {"area": "pavilosta", "level": 0.0, "sea": [2.5, 4.3, 350], "wind": [16, 10]},
    # An ordinary kiting day there: an onshore W-SW blow.
    "pav-day": {"area": "pavilosta", "level": 0.0, "sea": [1.2, 4.5, 290], "wind": [11, 290]},
    # Berzciems's bank at three levels (the owner, 25.11.2026: "waves brake before sandbanks ... most likely at least 1m
    # depth waves comes futher"): a NE blow across the gulf.
    "berz-low": {"area": "berzciems", "level": -0.3, "sea": [1.3, 4.6, 45], "wind": [12, 45]},
    "berz-mid": {"area": "berzciems", "level": 0.0, "sea": [1.3, 4.6, 45], "wind": [12, 45]},
    "berz-high": {"area": "berzciems", "level": 1.0, "sea": [1.3, 4.6, 45], "wind": [12, 45]},
    # Liepaja's harbour in a W storm (the owner, 19.11 and 22.11.2026: chop inside, calm behind the north mole).
    "liep-w": {"area": "liepaja-mols", "level": 0.0, "sea": [2.0, 6.0, 270], "wind": [14, 270]},
}
FIELDS = "HSIGN TPS TM01 DIR DSPR QB DISSURF DISWCAP DEPTH"


def _areas():
    return {a["area"]: a for a in T._json(T.STORE + "index.json")["areas"]}


def _pin(spot):
    for s in T._json(T.CATALOG)["spots"]:
        if s["id"] == spot:
            return s["spot"]["lat"], s["spot"]["lon"]
    return None


def prep(out, names=None):
    areas, built = _areas(), {}
    for name, case in CASES.items():
        if names and name not in names:
            continue
        a = areas[case["area"]]
        box, W, H = a["box"], a["W"], a["H"]
        home = Path(out) / name
        (home / "runs").mkdir(parents=True, exist_ok=True)
        if case["area"] not in built:
            built[case["area"]] = A.build(box, W, H, [], T.bars_for(case["area"]))
        depth = built[case["area"]]
        np.save(home / "depth.npy", depth)
        head, tail = T.swan_files(home, depth, box, W, H, case["level"], FIELDS)
        (Hs, Tp, way), (U, wway) = case["sea"], case["wind"]
        sides = "".join(f"BOUNDSPEC SIDE {s} CCW CONSTANT PAR {Hs} {Tp} {way} 20\n" for s in ("WEST", "NORTH", "EAST", "SOUTH"))
        bound = "BOUND SHAPESPEC JONSWAP 3.3 PEAK DSPR DEGREES\n" + sides
        runs = {
            # (As the tables' w and c runs: tables.py make.)
            "w": f"{head}{bound}GEN3\nOFF WCAPPING\nOFF QUADRUPL\n{tail}",
            "c": f"{head}WIND {U} {wway}\nGEN3\n{tail}",
            # Both at once, every process on: the real sea.
            "b": f"{head}{bound}WIND {U} {wway}\nGEN3\n{tail}",
        }
        for r, body in runs.items():
            (home / "runs" / r).mkdir(exist_ok=True)
            (home / "runs" / r / "INPUT").write_text(f"PROJECT 'ref' '1' '{name}'\n" + body)
        pin = _pin(a["spots"][0]) if a.get("spots") else None
        (home / "case.json").write_text(json.dumps({**case, "name": name, "box": box, "W": W, "H": H, "pin": pin}))
        print(name, "ready", flush=True)


def _load(run):
    from scipy.io import loadmat

    m = loadmat(run / "out.mat")
    out = {}
    for want, prefix in (("H", "hsig"), ("Tp", "tpsmoo"), ("Tm", "tm01"), ("dir", "dir"), ("spr", "dspr"), ("qb", "qb"),
                         ("surf", "ssurf"), ("wcap", "swcap")):
        k = next((k for k in m if k.lower().startswith(prefix)), None)
        if k is not None:
            out[want] = np.nan_to_num(np.asarray(m[k], dtype=float), nan=0.0)
    return out


def glue(w, c, open_h):
    """The map's glue today (swan.js swanNow, no swell): the sea's run's energy plus our wind's, the wind's less by K^2 (the
    sea's share of the open sea's height), as Hs."""
    K = np.minimum(w["H"] / open_h, 1.0) if open_h > 0 else np.zeros_like(w["H"])
    return np.sqrt(w["H"] ** 2 + c["H"] ** 2 * (1 - K ** 2))


def glues(w, c, open_h, depth):
    """Candidates for the tables' new glue, scored against the combined runs: the map's today, and the wind's chop
    taken out where the sea's waves already fill the depth (a breaking sea can't hold more: SWAN's own depth limit), as a
    cap on the total (cap) or by the sea's saturation (sat), at a few breaker ratios."""
    E = {"now": glue(w, c, open_h)}
    K = np.minimum(w["H"] / open_h, 1.0) if open_h > 0 else np.zeros_like(w["H"])
    d = np.maximum(depth, 0.05)
    for g in (0.5, 0.6, 0.7):
        E[f"cap{g}"] = np.minimum(E["now"], np.maximum(w["H"], g * d))
        S = np.minimum(w["H"] / (g * d), 1.0) ** 2
        E[f"sat{g}"] = np.sqrt(w["H"] ** 2 + c["H"] ** 2 * (1 - K ** 2) * (1 - S))
    return E


def report(out, names=None):
    for home in sorted(Path(out).iterdir()):
        if not (home / "case.json").exists() or (names and home.name not in names):
            continue
        case = json.loads((home / "case.json").read_text())
        if not all((home / "runs" / r / "out.mat").exists() for r in "wcb"):
            print(home.name, "not run yet")
            continue
        w, c, b = (_load(home / "runs" / r) for r in "wcb")
        depth = np.load(home / "depth.npy") + case["level"]
        sea = (b["H"] > 0.01) & (depth > 0.05)
        g = glue(w, c, case["sea"][0])
        print(f"\n== {home.name}: level {case['level']:+g} m, sea {case['sea'][0]} m {case['sea'][1]} s from {case['sea'][2]}, "
              f"wind {case['wind'][0]} m/s from {case['wind'][1]}")
        print("depth band   cells  both Hs  glued  w only  c only  glued/both  >20% off  spread  breaking  surf/wcap")
        for lo, hi in ((6, 99), (3, 6), (1.5, 3), (0.7, 1.5), (0.05, 0.7)):
            m = sea & (depth >= lo) & (depth < hi)
            if m.sum() < 20:
                continue
            ratio = g[m] / np.maximum(b["H"][m], 0.02)
            surf, wcap = b.get("surf", np.zeros_like(depth))[m].mean(), b.get("wcap", np.zeros_like(depth))[m].mean()
            print(f"{lo:>4}-{hi:<4} m {m.sum():>7}  {np.median(b['H'][m]):6.2f}  {np.median(g[m]):6.2f}  {np.median(w['H'][m]):6.2f}  "
                  f"{np.median(c['H'][m]):6.2f}  {np.median(ratio):9.2f}  {np.mean(np.abs(ratio - 1) > 0.2) * 100:6.0f} %  "
                  f"{np.median(b.get('spr', np.zeros_like(depth))[m]):5.0f}°  {np.mean(b.get('qb', np.zeros_like(depth))[m]):7.2f}  "
                  f"{surf / max(wcap, 1e-9):8.1f}")
        print("glue       all cells: median, >20% off | under 1.5 m: median, >20% off")
        for k, v in glues(w, c, case["sea"][0], depth).items():
            r = v[sea] / np.maximum(b["H"][sea], 0.02)
            m = depth[sea] < 1.5
            print(f"{k:8}  {np.median(r):17.2f} {np.mean(np.abs(r - 1) > 0.2) * 100:5.0f} % | {np.median(r[m]):16.2f} "
                  f"{np.mean(np.abs(r[m] - 1) > 0.2) * 100:5.0f} %")
        if not case.get("pin"):
            continue
        # The line: from 4 km up-wave (the sea's way) to the pin, every 200 m.
        box, W, H = case["box"], case["W"], case["H"]
        lat, lon = case["pin"]
        px = ((lon + 180) / 360 - box[0]) / box[2] * W - 0.5
        py = ((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 - box[1]) / box[3] * H - 0.5
        cell = box[2] * A.EARTH * math.cos(math.radians(lat)) / W
        fr = math.radians(case["sea"][2])
        print(" from pin   depth  both Hs Tp  spread  breaking | glued  w only  c only")
        for k in range(20, -1, -1):
            d = k * 200
            x, y = px + math.sin(fr) * d / cell, py - math.cos(fr) * d / cell
            i, j = int(round(y)), int(round(x))
            if not (0 <= i < H and 0 <= j < W):
                continue
            if not sea[i, j]:
                print(f"{d:>7} m  land/dry")
                continue
            print(f"{d:>7} m  {depth[i, j]:5.1f}  {b['H'][i, j]:5.2f} {b['Tp'][i, j]:4.1f}  {b.get('spr', depth)[i, j]:5.0f}°  "
                  f"{b.get('qb', depth * 0)[i, j]:6.2f}   | {g[i, j]:5.2f}  {w['H'][i, j]:5.2f}  {c['H'][i, j]:5.2f}")


if __name__ == "__main__":
    cmd, out = sys.argv[1], sys.argv[2]
    rest = sys.argv[3].split(",") if len(sys.argv) > 3 and cmd == "prep" else sys.argv[3:]
    {"prep": prep, "report": report}[cmd](out, rest or None)
