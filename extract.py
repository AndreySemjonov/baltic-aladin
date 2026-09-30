"""ALADIN 2.3 km wind at Baltic kite spots, from ČHMÚ open data.

Finds the newest ALADIN run on https://opendata.chmi.cz/ (Lambert 2.3 km domain),
downloads 10 m wind speed, wind direction and the two gust components, reads each
spot in spots.json and writes docs/aladin.json. Each value is the four grid points
around the spot weighted by inverse distance; direction is their speed-weighted
vector mean. Spots whose nearest grid point is more than 3 km away are outside
the model's area and listed under "outside".

    python extract.py            # newest run, only if it is new
    python extract.py 2026093006 # a given run
"""
import bz2
import json
import math
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes as ec

BASE = "https://opendata.chmi.cz/meteorology/weather/nwp_aladin/Lambert_2.3km"
VARIABLES = {"speed": "CLSWIND_SPEED", "direction": "CLSWIND_DIREC",
             "gust_u": "CLSU_RAF_MOD_XFU", "gust_v": "CLSV_RAF_MOD_XFU"}
HERE = Path(__file__).parent
OUTPUT = HERE / "docs" / "aladin.json"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"


def url(run, variable):
    return f"{BASE}/{run[-2:]}/ALADLAMB4opendata_{run}_{variable}.grb.bz2"


def available(run):
    """True when all four files of the run are on the server."""
    for variable in VARIABLES.values():
        request = urllib.request.Request(url(run, variable), method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as reply:
                if reply.status != 200:
                    return False
        except urllib.error.URLError:
            return False
    return True


def newest_run(now):
    """The newest run of the last 30 hours with all files published (runs every 6 h)."""
    hour = now.replace(minute=0, second=0, microsecond=0)
    start = hour - timedelta(hours=hour.hour % 6)
    for back in range(0, 30, 6):
        run = (start - timedelta(hours=back)).strftime("%Y%m%d%H")
        if available(run):
            return run
    return None


def download(run, variable, folder):
    """Stream the bz2 file and unpack it on the fly into `folder`."""
    path = folder / f"{variable}.grb"
    request = urllib.request.Request(url(run, variable), headers={"User-Agent": USER_AGENT})
    decompressor = bz2.BZ2Decompressor()
    with urllib.request.urlopen(request, timeout=600) as reply, open(path, "wb") as out:
        while chunk := reply.read(1 << 20):
            out.write(decompressor.decompress(chunk))
    return path


def read(path, spots):
    """{unix time: {spot id: [values of the 4 nearest points]}}, and the weights and
    nearest distance (km) of each spot's points."""
    values, geometry = {}, None
    with open(path, "rb") as f:
        while (gid := ec.codes_grib_new_from_file(f)) is not None:
            try:
                date, hhmm = ec.codes_get(gid, "validityDate"), ec.codes_get(gid, "validityTime")
                t = int(datetime.strptime(f"{date}{hhmm:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp())
                if geometry is None:
                    geometry = {}
                    for spot in spots:
                        near = ec.codes_grib_find_nearest(gid, spot["lat"], spot["lon"], npoints=4)
                        geometry[spot["id"]] = ([p["index"] for p in near],
                                                [1 / max(p["distance"], 0.01) for p in near],
                                                min(p["distance"] for p in near))
                values[t] = {sid: list(ec.codes_get_double_elements(gid, "values", g[0]))
                             for sid, g in geometry.items()}
            finally:
                ec.codes_release(gid)
    return values, geometry


def weighted(weights, vals):
    return sum(w * v for w, v in zip(weights, vals)) / sum(weights)


def direction(weights, degrees, speeds):
    x = sum(w * s * math.sin(math.radians(d)) for w, d, s in zip(weights, degrees, speeds))
    y = sum(w * s * math.cos(math.radians(d)) for w, d, s in zip(weights, degrees, speeds))
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return None
    return round((math.degrees(math.atan2(x, y)) + 360) % 360)


def extract(run, spots):
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        fields = {}
        geometry = None
        for key, variable in VARIABLES.items():
            fields[key], geometry = read(download(run, variable, folder), spots)
    times = sorted(fields["speed"])
    inside = [s for s in spots if geometry[s["id"]][2] <= 3.0]
    out = {}
    for spot in inside:
        sid = spot["id"]
        weights = geometry[sid][1]
        wind, gust, dirs = [], [], []
        for t in times:
            speeds = fields["speed"][t][sid]
            wind.append(round(weighted(weights, speeds), 2))
            gusts = [math.hypot(u, v) for u, v in zip(fields["gust_u"][t][sid], fields["gust_v"][t][sid])]
            gust.append(round(weighted(weights, gusts), 2))
            dirs.append(direction(weights, fields["direction"][t][sid], speeds))
        out[sid] = {"wind": wind, "gust": gust, "direction": dirs}
    run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return {
        "model": "ALADIN 2.3 km",
        "source": "ČHMÚ (Czech Hydrometeorological Institute) open data, CC BY 4.0: https://opendata.chmi.cz/",
        "run": run_time.strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "units": {"wind": "m/s", "gust": "m/s", "direction": "degrees, where the wind comes from"},
        "method": "4 grid points around each spot, inverse-distance weighted; no coastal (CS+) shift",
        "times": times,
        "spots": out,
        "outside": [s["id"] for s in spots if s not in inside],
    }


def main():
    spots = json.loads((HERE / "spots.json").read_text(encoding="utf-8"))
    run = sys.argv[1] if len(sys.argv) > 1 else newest_run(datetime.now(timezone.utc))
    if run is None:
        print("No complete ALADIN run found")
        return
    if len(sys.argv) == 1 and OUTPUT.exists():
        current = json.loads(OUTPUT.read_text(encoding="utf-8")).get("run", "")
        if current == datetime.strptime(run, "%Y%m%d%H").strftime("%Y-%m-%dT%H:%MZ"):
            print(f"Run {run} already published")
            return
    data = extract(run, spots)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Run {run}: {len(data['spots'])} spots, {len(data['times'])} hours; outside: {data['outside']}")


if __name__ == "__main__":
    main()
