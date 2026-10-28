"""Road distances to the spots (the owner, 29.10.2026: "Where to kite" and the Nearest sort by the road, not the
straight line: from Saulkrasti, Bērzciems is 50 km across the Gulf of Riga and ~200 km by car).

For each place (a catalog spot, or one asked for with --at), a grid of the driving time and distance TO it from every
~4 km cell within 300 km, from OpenStreetMap's roads (and ferries) with OSRM's car profile. The phone downloads the
files for its cards and looks up the rider's cell offline: no routing service, no location sent anywhere.

File tiles/roads/v1/<lat>_<lon>.bin (the place rounded to 0.01°, as the phones round it), little-endian:
  "MGRD", version u8 (1), cell km u8, n u16 (the grid is 2n x 2n), lat f32, lon f32 (the rounded place),
  then raw DEFLATE (zlib wbits -15) of 2n*2n pairs [minutes/2, km/2] (u8 each; 255 = no road), rows from the south,
  each row from the west. Cell (i, j) holds the point
    lat = lat0 + (i + 0.5 - n) * cell / 110.574,  lon = lon0 + (j + 0.5 - n) * cell / (111.32 * cos(lat0))
tiles/roads/v1/index.json: {"places": ["57.31_24.40", ...]}, the files there.

OpenStreetMap data © OpenStreetMap contributors (ODbL); these files are a derived database under the ODbL too.

Run: python roads.py [--region baltic] [--at LAT,LON] [--local DIR] [--check]   (needs docker and osmium-tool;
GitHub's roads.yml). --local writes the files to DIR instead of uploading them; --check only prints the CHECKS drives
against Google's times (nothing made).
"""
import argparse
import re
import json
import math
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib

CATALOG = "https://data.meteogust.com/spots/catalog"
FILES = "https://files.meteogust.com/"
UPLOAD = "https://data.meteogust.com/tiles/upload/"
FOLDER = "roads/v1"                 # under tiles/ on our files (files.meteogust.com/tiles/roads/v1/...)
AGENT = "MeteoGust helper"
OSRM_IMAGE = "ghcr.io/project-osrm/osrm-backend:v5.27.1"
CELL_KM = 4
RADIUS_KM = 300
N = RADIUS_KM // CELL_KM            # 75: a 150 x 150 grid
SNAP_M = 2500                       # a cell farther than this from a road (the sea, a forest) has none
CHUNK = 300                         # cells per table request (the URL stays under 8 KB)

# OSRM's car profile drives slow here: 80 % of the speed limit, and untagged roads at 55 km/h (secondary) or 40
# (tertiary) where Latvia's are 90 by law. Its speeds raised to match Google's times without traffic (the owner,
# 29.10.2026: "usually google maps right about distances", three drives from Riga in CHECKS); --check prints them.
PROFILE = {"speed_reduction": 0.95, "trunk": 90, "primary": 85, "secondary": 75, "tertiary": 60, "unclassified": 45}

# Google's drives without traffic (minutes, km; the owner's maps, 29.10.2026) from west Riga by Babīte.
CHECKS = [
    ("Riga → Kolka", (56.952, 24.005), (57.759, 22.607), 126, 156),
    ("Riga → Häädemeeste", (56.952, 24.005), (58.080, 24.474), 125, 149),
    ("Riga → Liepāja's breakwater", (56.952, 24.005), (56.558, 21.001), 168, 211),     # with traffic
]

# The regions with spots, each one OpenStreetMap file from Geofabrik's extracts (merged when several).
# A place outside every box gets no file (the phones keep the straight line there).
REGIONS = {
    "baltic": {"extracts": ["europe/latvia", "europe/estonia", "europe/lithuania"],
               "box": (53.8, 20.0, 59.9, 28.3)},      # south, west, north, east
    "andalucia": {"extracts": ["europe/spain/andalucia"],
                  "box": (35.9, -7.6, 38.8, -1.6)},
}


def key(lat, lon):
    return f"{lat:.2f}_{lon:.2f}"


def region_of(lat, lon):
    for name, region in REGIONS.items():
        s, w, n, e = region["box"]
        if s <= lat <= n and w <= lon <= e:
            return name
    return None


def fetch(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply.read()


def upload(path, data):
    """PUT on our server (station-history /tiles/upload, the INGEST_TOKEN secret), with retries."""
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    for attempt in range(4):
        try:
            request = urllib.request.Request(UPLOAD + path, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=120) as reply:
                return reply.read().decode()
        except (urllib.error.URLError, OSError) as error:
            if (isinstance(error, urllib.error.HTTPError) and error.code < 500) or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def run(*command):
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def prepare(region, work):
    """The region's roads for OSRM in `work`: downloaded, merged, extracted with the car profile, partitioned."""
    os.makedirs(work, exist_ok=True)
    extracts = REGIONS[region]["extracts"]
    merged = f"{region}.osm.pbf"
    parts = []
    for extract in extracts:
        name = merged if len(extracts) == 1 else extract.replace("/", "-") + ".osm.pbf"
        if not os.path.exists(os.path.join(work, name)):
            run("curl", "-sSfL", "-A", AGENT, "-o", os.path.join(work, name),
                f"https://download.geofabrik.de/{extract}-latest.osm.pbf")
        parts.append(os.path.join(work, name))
    if len(parts) > 1:
        run("osmium", "merge", "--overwrite", "-o", os.path.join(work, merged), *parts)
    profile(work)
    docker = ["docker", "run", "--rm", "-v", f"{os.path.abspath(work)}:/data",
              "-v", f"{os.path.abspath(work)}/car.lua:/opt/car.lua:ro", OSRM_IMAGE]
    run(*docker, "osrm-extract", "-p", "/opt/car.lua", f"/data/{merged}")
    run(*docker, "osrm-partition", f"/data/{region}.osrm")
    run(*docker, "osrm-customize", f"/data/{region}.osrm")


def profile(work):
    """OSRM's car profile with our speeds (PROFILE) as work/car.lua, put over the image's own (its lib/ stays)."""
    text = subprocess.run(["docker", "run", "--rm", OSRM_IMAGE, "cat", "/opt/car.lua"],
                          check=True, capture_output=True, text=True).stdout
    for name, value in PROFILE.items():
        text, count = re.subn(rf"(\n\s+{name}\s+=\s+)[0-9.]+", rf"\g<1>{value}", text, count=1)
        if count != 1:
            raise SystemExit(f"car.lua has no {name}")
    with open(os.path.join(work, "car.lua"), "w") as out:
        out.write(text)


def check():
    """The CHECKS drives by our roads against Google's."""
    for name, (lat1, lon1), (lat2, lon2), minutes, km in CHECKS:
        route = json.loads(fetch(f"http://127.0.0.1:5000/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"))["routes"][0]
        ours = route["duration"] / 60
        print(f"{name}: ours {ours:.0f} min {route['distance'] / 1000:.0f} km, Google {minutes} min {km} km: "
              f"x{ours / minutes:.2f}", flush=True)


def serve(region, work):
    """OSRM's server on localhost:5000 for the region; returns its container's id."""
    container = subprocess.run(
        ["docker", "run", "-d", "--rm", "-p", "5000:5000", "-v", f"{os.path.abspath(work)}:/data", OSRM_IMAGE,
         "osrm-routed", "--algorithm", "mld", "--max-table-size", str(CHUNK + 10), f"/data/{region}.osrm"],
        check=True, capture_output=True, text=True).stdout.strip()
    for _ in range(120):
        try:
            fetch("http://127.0.0.1:5000/nearest/v1/driving/0,0", timeout=5)
            break
        except urllib.error.HTTPError:
            break                   # it answers (no road at 0,0)
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return container


def cells(lat0, lon0):
    """The grid's cells within the radius: (index, lat, lon)."""
    out = []
    east = CELL_KM / (111.32 * math.cos(math.radians(lat0)))
    north = CELL_KM / 110.574
    for i in range(2 * N):
        for j in range(2 * N):
            y, x = (i + 0.5 - N) * CELL_KM, (j + 0.5 - N) * CELL_KM
            if x * x + y * y > RADIUS_KM * RADIUS_KM:
                continue
            out.append((i * 2 * N + j, lat0 + (i + 0.5 - N) * north, lon0 + (j + 0.5 - N) * east))
    return out


def grid(lat0, lon0):
    """The place's file: every cell's drive to it."""
    values = bytearray([255]) * (2 * N * 2 * N * 2)
    todo = cells(lat0, lon0)
    reached = 0
    for start in range(0, len(todo), CHUNK):
        chunk = todo[start:start + CHUNK]
        points = ";".join(f"{lon:.5f},{lat:.5f}" for _, lat, lon in chunk) + f";{lon0:.5f},{lat0:.5f}"
        url = (f"http://127.0.0.1:5000/table/v1/driving/{points}?sources=" + ";".join(map(str, range(len(chunk))))
               + f"&destinations={len(chunk)}&annotations=duration,distance")
        answer = json.loads(fetch(url))
        if answer.get("code") != "Ok":
            raise SystemExit(f"OSRM: {answer.get('code')} {answer.get('message')}")
        if answer["destinations"][0].get("distance", 0) > SNAP_M:
            return None, 0          # the place itself is far from any road in this region
        for (index, _, _), source, durations, distances in zip(chunk, answer["sources"], answer["durations"],
                                                               answer["distances"]):
            duration, distance = durations[0], distances[0]
            if source.get("distance", SNAP_M + 1) > SNAP_M or duration is None or distance is None:
                continue
            values[2 * index] = min(254, round(duration / 120))
            values[2 * index + 1] = min(254, round(distance / 2000))
            reached += 1
    header = b"MGRD" + struct.pack("<BBHff", 1, CELL_KM, N, lat0, lon0)
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    return header + packer.compress(bytes(values)) + packer.flush(), reached


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", help="only this region's places")
    parser.add_argument("--at", action="append", default=[], help="LAT,LON: this place too (or only, with no catalog)")
    parser.add_argument("--only", action="store_true", help="only the --at places, not the catalog's")
    parser.add_argument("--local", help="write the files here instead of uploading them")
    parser.add_argument("--work", default="roads-work")
    parser.add_argument("--check", action="store_true", help="only the CHECKS drives (the Baltic region), nothing made")
    args = parser.parse_args()

    places = {}
    if not args.only:
        for entry in json.loads(fetch(CATALOG))["spots"]:
            spot = entry["spot"]
            places[key(spot["lat"], spot["lon"])] = entry["id"]
    for at in args.at:
        lat, lon = (float(v) for v in at.split(","))
        places[key(lat, lon)] = at
    if args.check:
        work = os.path.join(args.work, "baltic")
        prepare("baltic", work)
        container = serve("baltic", work)
        try:
            check()
        finally:
            subprocess.run(["docker", "stop", container], capture_output=True)
        return
    by_region = {}
    for place, name in sorted(places.items()):
        lat, lon = (float(v) for v in place.split("_"))
        region = region_of(lat, lon)
        if region is None:
            print(f"{name} ({place}): in no region, left out")
        elif not args.region or region == args.region:
            by_region.setdefault(region, []).append((place, name))

    try:
        index = set(json.loads(fetch(FILES + "tiles/" + FOLDER + "/index.json?t=" + str(int(time.time()))))["places"])
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        index = set()
    made = 0
    for region, todo in by_region.items():
        work = os.path.join(args.work, region)
        prepare(region, work)
        container = serve(region, work)
        try:
            for place, name in todo:
                lat, lon = (float(v) for v in place.split("_"))
                data, reached = grid(lat, lon)
                if data is None:
                    print(f"{name} ({place}): no road near it, left out")
                    continue
                print(f"{name} ({place}): {reached} cells by road, {len(data)} bytes", flush=True)
                if args.local:
                    os.makedirs(os.path.join(args.local, FOLDER), exist_ok=True)
                    with open(os.path.join(args.local, FOLDER, place + ".bin"), "wb") as out:
                        out.write(data)
                else:
                    upload(f"{FOLDER}/{place}.bin", data)
                index.add(place)
                made += 1
        finally:
            subprocess.run(["docker", "stop", container], capture_output=True)
    body = json.dumps({"places": sorted(index)}, separators=(",", ":")).encode()
    if args.local:
        with open(os.path.join(args.local, FOLDER, "index.json"), "wb") as out:
            out.write(body)
    elif made:
        upload(f"{FOLDER}/index.json", body)
    print(f"{made} places made; {len(index)} in the index")


if __name__ == "__main__":
    sys.exit(main())
