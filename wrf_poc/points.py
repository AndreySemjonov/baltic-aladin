"""A WRF run's wind at a few points (the owner, 17.10.2026: check our WRF against the airports'
reports, which are measured by the rules, not against beach stations that read off): every hour the
10 m wind (m/s), the direction it comes from (°) and the gust (from the boundary layer, gust.py) from
the finest box that covers the point, into <work>/points.json, uploaded as
tiles/wrf-<region>/points/<run>.json (directly with INGEST_TOKEN, else with the packed tiles).

    REGION=tarifa python wrf_poc/points.py work [OUT]
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wrf_poc import axes  # noqa: E402
from wrf_poc import gust as gusts  # noqa: E402

_GUSTS = {}

# name: (lat, lon, kind); airports by their ICAO code (their reports: station-history's METARs).
POINTS = {
    "tarifa": {
        "LXGB": (36.151, -5.349, "airport"), "GMTT": (35.727, -5.917, "airport"), "LEJR": (36.745, -6.060, "airport"),
        "Tarifa": (36.012, -5.606, "spot"), "Valdevaqueros": (36.067, -5.690, "spot"), "Getares": (36.105, -5.432, "spot"),
    },
    "capetown": {
        "FACT": (-33.969, 18.597, "airport"),
        "Bloubergstrand": (-33.800, 18.460, "spot"), "Muizenberg": (-34.108, 18.472, "spot"), "Langebaan": (-33.090, 18.030, "spot"),
        # The Breede valley's 1 km box (the owner, 02.11.2026: thermals when Cape Town has no wind).
        "Brandvlei Dam": (-33.720, 19.430, "spot"),
    },
}


def series(path, lat, lon):
    """The point's hours from one wrfout: (unix times, speed, from-direction, gust), or None when the
    point is outside the box (more than 2 cells from its nearest)."""
    with Dataset(path) as data:
        xlat, xlon = np.asarray(data["XLAT"][0]), np.asarray(data["XLONG"][0])
        d2 = (xlat - lat) ** 2 + ((xlon - lon) * math.cos(math.radians(lat))) ** 2
        j, i = np.unravel_index(np.argmin(d2), d2.shape)
        dx = float(data.DX) / 1000
        if math.sqrt(d2[j, i]) * 111 > 2 * dx or j in (0, xlat.shape[0] - 1) or i in (0, xlat.shape[1] - 1):
            return None
        u, v = np.asarray(data["U10"][:, j, i]), np.asarray(data["V10"][:, j, i])
        # The grid's axes to east and north (axes.py; COSALPHA/SINALPHA turned Cape Town's the wrong way).
        cos_b, sin_b = axes.grid_north(xlat, xlon)
        east, north = axes.to_earth(u, v, cos_b[j, i], sin_b[j, i])
        # The whole grid's gusts once a file (every point reads them).
        if path not in _GUSTS:
            _GUSTS[path] = gusts.all_hours(data)
        gust = _GUSTS[path][:, j, i]
        times = [int(datetime.strptime("".join(c.decode() for c in t), "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                 for t in data["Times"][:]]
    speed = np.hypot(east, north)
    direction = np.degrees(np.arctan2(-east, -north)) % 360
    return times, speed, direction, np.maximum(gust, speed), dx


def main():
    work = Path(sys.argv[1] if len(sys.argv) > 1 else "work")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else work / "points.json"
    region = os.environ.get("REGION", "tarifa")
    env = dict(line.split("=", 1) for line in (work / "env").read_text().split() if "=" in line)
    # Every box the run has (d01-d03, Cape Town's valley box d04), coarse first.
    domains = sorted({p.name.split("_")[1] for p in (work / "wrf").glob("wrfout_d0*")})
    files = {d: sorted((work / "wrf").glob(f"wrfout_{d}_*")) for d in domains}
    result = {"region": region, "run": env["CYCLE"], "made": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
              "units": "m/s, from degrees", "points": {}}
    for name, (lat, lon, kind) in POINTS.get(region, {}).items():
        hours = {}
        # Coarse first, then finer over it: each hour from the finest box that has it.
        for domain in domains:
            for path in files[domain]:
                found = series(path, lat, lon)
                if not found:
                    continue
                times, speed, direction, gust, dx = found
                for k, t in enumerate(times):
                    hours[t] = [t, round(float(speed[k]), 2), round(float(direction[k])), round(float(gust[k]), 2), round(dx, 1)]
        result["points"][name] = {"lat": lat, "lon": lon, "kind": kind, "hours": [hours[t] for t in sorted(hours)],
                                  "columns": ["time", "speed", "from", "gust", "km"]}
    out.write_text(json.dumps(result, separators=(",", ":")))
    counts = [name + " " + str(len(point["hours"])) + " h" for name, point in result["points"].items()]
    print("points: " + ", ".join(counts), flush=True)


if __name__ == "__main__":
    main()
