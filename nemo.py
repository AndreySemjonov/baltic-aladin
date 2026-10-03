"""Sea level and water temperature at the Baltic kite spots from NEMO-EST, the Estonian
Environment Agency's sea model (with TalTech; about 1 km, hourly, 5 days, twice a day;
open data, CC BY 4.0, https://keskkonnaportaal.ee/et/avaandmed/ilma-mudelprognoosid).

Finds the newest run's surface files (one per day, about 75 MB each) through the agency's
open data API, reads each spot's nearest sea cell and writes docs/nemo.json: per spot the
sea surface height (cm above the model's geoid) and the sea surface temperature (°C) for
every hour. The model covers Estonia and the Gulf of Riga (from about 56.94°N, 21.55°E);
spots without a sea cell within 5 km are listed under "outside".

    python nemo.py           # newest run, only if it is new
    python nemo.py --force   # newest run even if published
"""
import json
import math
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import h5py
import numpy as np

from water_map import WaterMap

API = "https://avaandmed.keskkonnaportaal.ee/api/lists/active"
NEMO = "0102FB02"  # the API's content type of the NEMO files
HERE = Path(__file__).parent
OUTPUT = HERE / "docs" / "nemo.json"
# Points along the whole coast, for spots the helper doesn't know (a phone's own map pins):
# not kept in git (about 1 MB), published with the site and carried over between deploys.
COAST_OUTPUT = HERE / "nemo-coast.json"
# Sea cells within about 4 km of the land, every third cell each way (about 3 km apart).
COAST_CELLS, COAST_STEP = 4, 3
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"
REACH_KM = 5
NAME = re.compile(r"nemo_(\d{10})_EST05nm_op_run1_1h_SURF_grid_TUV\.(\d{8})\.nc$")


def newest_files(now):
    """The newest run with its daily surface files: (run, [(day, item id), ...])."""
    query = {"filter": {"and": {"children": [
        {"underContentType": {"contentType": NEMO}},
        {"contains": {"field": "RMTitle", "value": "SURF_grid"}},
        {"greaterThan": {"field": "FP.CreatedAt", "value": (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")}},
    ]}}, "pageSize": 500, "fields": ["RMTitle"]}
    request = urllib.request.Request(f"{API}/items/query", data=json.dumps(query).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as reply:
        documents = json.load(reply)["documents"]
    runs = {}
    for document in documents:
        match = NAME.match(document["metadata"].get("RMTitle") or "")
        if match:
            runs.setdefault(match.group(1), []).append((match.group(2), document["id"]))
    if not runs:
        return None, []
    run = max(runs)
    return run, sorted(runs[run])


def download(item, path):
    request = urllib.request.Request(f"{API}/items/{item}/files/1", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=600) as reply, open(path, "wb") as out:
        while chunk := reply.read(1 << 20):
            out.write(chunk)


def coast_published(run):
    """Whether the live site has this run's coast points and water map (they aren't kept in git)."""
    site = "https://andreysemjonov.github.io/baltic-aladin/"
    run_time = datetime.strptime(run, "%Y%m%d%H").strftime("%Y-%m-%dT%H:%MZ")
    try:
        for path, wanted in (("nemo-coast.json", run), ("water/map.json", run_time)):
            with urllib.request.urlopen(urllib.request.Request(site + path, headers={"User-Agent": USER_AGENT}),
                                        timeout=60) as reply:
                published = json.load(reply)
                # A water map from before its run's ranges were written is made again.
                if published.get("run") != wanted or (path.startswith("water") and "levelRange" not in published):
                    return False
        return True
    except Exception:
        return False


def sea_cell(lat, lon, lats, lons, sea):
    """The nearest sea cell within REACH_KM of a spot, or None."""
    j0, i0 = int(np.abs(lats - lat).argmin()), int(np.abs(lons - lon).argmin())
    best = None
    for j in range(max(j0 - 12, 0), min(j0 + 13, len(lats))):
        for i in range(max(i0 - 12, 0), min(i0 + 13, len(lons))):
            if not sea[j, i]:
                continue
            km = math.hypot((lats[j] - lat) * 111.2, (lons[i] - lon) * 111.2 * math.cos(math.radians(lat)))
            if km <= REACH_KM and (best is None or km < best[0]):
                best = (km, j, i)
    return best


def main():
    force = "--force" in sys.argv
    now = datetime.now(timezone.utc)
    run, files = newest_files(now)
    if not run:
        print("No NEMO files found")
        return
    try:
        published = json.loads(OUTPUT.read_text(encoding="utf-8")).get("run")
    except (OSError, ValueError):
        published = None
    if run == published and not force and coast_published(run):
        print(f"NEMO {run} already published")
        return
    spots = json.loads((HERE / "spots.json").read_text(encoding="utf-8"))
    times, cells = [], {}
    water = None
    coast_cells, coast_level, coast_temperature = None, [], []
    series = {spot["id"]: {"level": [], "temperature": []} for spot in spots}
    with tempfile.TemporaryDirectory() as folder:
        for day, item in files:
            path = Path(folder) / f"{day}.nc"
            print(f"NEMO {run} {day}: downloading", flush=True)
            download(item, path)
            with h5py.File(path, "r") as f:
                if not cells:
                    lats, lons = f["lat"][:], f["lon"][:]
                    sea = f["SST"][0] < 1e10
                    for spot in spots:
                        found = sea_cell(spot["lat"], spot["lon"], lats, lons, sea)
                        if found:
                            cells[spot["id"]] = found
                    from scipy import ndimage
                    near_land = sea & (ndimage.distance_transform_edt(sea) <= COAST_CELLS)
                    rows, columns = np.nonzero(near_land)
                    keep = (rows % COAST_STEP == 0) & (columns % COAST_STEP == 0)
                    coast_cells = (rows[keep], columns[keep])
                    water = WaterMap(run, lats, lons, sea, sea_cell)
                base = datetime(1800, 1, 1, tzinfo=timezone.utc)
                file_times = [int((base + timedelta(seconds=float(t))).timestamp()) for t in f["time_counter"][:]]
                times += file_times
                water.add_file(file_times, f)
                ssh, sst = f["SSH"], f["SST"]
                # Whole fields per hour, then the coast points (reading point by point is slow).
                for k in range(ssh.shape[0]):
                    level, temperature = ssh[k][coast_cells], sst[k][coast_cells]
                    coast_level.append([int(round(float(v) * 100)) if abs(v) < 1e10 else None for v in level])
                    coast_temperature.append([int(round(float(v) * 10)) if abs(v) < 1e10 else None for v in temperature])
                for spot_id, (_, j, i) in cells.items():
                    series[spot_id]["level"] += [round(float(v) * 100, 1) if abs(v) < 1e10 else None for v in ssh[:, j, i]]
                    series[spot_id]["temperature"] += [round(float(v), 2) if abs(v) < 1e10 else None for v in sst[:, j, i]]
    out = {
        "source": "NEMO-EST, Estonian Environment Agency (Keskkonnaagentuur) and TalTech, CC BY 4.0",
        "run": run,
        "made": now.strftime("%Y-%m-%dT%H:%MZ"),
        "start": times[0] if times else None,
        "step": 3600,
        "units": {"level": "cm above the model's geoid", "temperature": "degC"},
        "spots": {},
        "outside": [spot["id"] for spot in spots if spot["id"] not in cells],
    }
    if any(b - a != 3600 for a, b in zip(times, times[1:])):
        raise SystemExit("NEMO hours are not continuous")
    for spot in spots:
        if spot["id"] not in cells:
            continue
        km, j, i = cells[spot["id"]]
        out["spots"][spot["id"]] = {"lat": round(float(lats[j]), 4), "lon": round(float(lons[i]), 4), "km": round(km, 1),
                                    **series[spot["id"]]}
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    # Per point: its hours, level in cm and temperature in tenths of a degree.
    rows, columns = coast_cells
    coast = {"source": out["source"], "run": run, "made": out["made"], "start": out["start"], "step": 3600,
             "units": {"level": "cm above the model's geoid", "temperature": "degC x 10"},
             "points": [[round(float(lats[j]), 4), round(float(lons[i]), 4)] for j, i in zip(rows, columns)],
             "level": [list(x) for x in zip(*coast_level)],
             "temperature": [list(x) for x in zip(*coast_temperature)]}
    COAST_OUTPUT.write_text(json.dumps(coast, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"NEMO coast: {len(coast['points'])} points, {COAST_OUTPUT.stat().st_size / 1e6:.1f} MB")
    if water:
        water.finish()
    print(f"NEMO {run}: {len(times)} hours, {len(out['spots'])} spots, outside {out['outside']}")


if __name__ == "__main__":
    main()
