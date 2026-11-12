"""The map search's places (the owner, 12.11.2026: "GeoNames list on R2"): towns and the coast's named points from GeoNames,
one file our apps search themselves (no geocoder: the public ones forbid search-as-you-type, our own would be a 24/7
server). GeoNames' data is CC BY 4.0: credited in the apps (Credits.swift, docs/LICENCES.md).

Run: python places.py [--local DIR]   (GitHub's places.yml, once a year; --local writes the file to DIR instead of
uploading it to station-history /tiles/upload/places/v1/places.json with INGEST_TOKEN).

The file: {"made", "credit", "countries": [name], "regions": [name], "kinds": [name], "rows": "..."} with one place a line,
tab-separated: name, its ASCII spelling where it differs (else empty), lat x 1000, lon x 1000, kind (index, 0 a town),
country (index), region (index, -1 none), km to show north to south. Towns of 500 people and more (cities500: Kolka has
~800), and beaches, capes, spits, bays and lagoons from the whole dump; a feature named as a town within ~5 km left out
(the phone's rule: Kolka the village, not its cape twice).
"""

import argparse
import io
import json
import os
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

DUMP = "https://download.geonames.org/export/dump/"
UPLOAD = "https://data.meteogust.com/tiles/upload/"
AGENT = "MeteoGust-helper (places)"
# GeoNames' feature codes kept from the whole dump, with the word the search's row says (the phone's "Beach · ...").
# Not points (112,000, many on inland lakes) nor coves (47,000): they made the file 5.5 MB gzipped, little a rider looks for.
FEATURES = {"BCH": "Beach", "BCHS": "Beach", "CAPE": "Cape", "SPIT": "Spit", "BAY": "Bay", "BAYS": "Bay", "LGN": "Lagoon",
            "LGNS": "Lagoon"}
KINDS = [""] + sorted(set(FEATURES.values()))


def fetch(name, work):
    """A dump file, kept in `work` between runs of a day."""
    path = os.path.join(work, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
        return path
    for attempt in range(4):
        try:
            print("download", name, flush=True)
            request = urllib.request.Request(DUMP + name, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=600) as reply, open(path + ".part", "wb") as out:
                while chunk := reply.read(1 << 20):
                    out.write(chunk)
            os.replace(path + ".part", path)
            return path
        except (urllib.error.URLError, OSError):
            if attempt == 3:
                raise
            time.sleep(30 * (attempt + 1))


def lines(path, inner):
    """The text lines of a zipped dump (or a plain file when `inner` is None)."""
    if inner is None:
        with open(path, encoding="utf-8") as f:
            yield from f
        return
    with zipfile.ZipFile(path) as z, z.open(inner) as raw:
        yield from io.TextIOWrapper(raw, encoding="utf-8")


def fold(text):
    """As the apps' search folds (SearchMatch): the letters' marks off, lower case."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).lower()


def km_for(population):
    """How much of the map a town gets, north to south (the phone frames Apple's places by their size, 4 km at least)."""
    return 40 if population >= 1_000_000 else 20 if population >= 100_000 else 10 if population >= 10_000 else 6


def build(work):
    countries, country_index = [], {}
    for line in lines(fetch("countryInfo.txt", work), None):
        if line.startswith("#"):
            continue
        cells = line.rstrip("\n").split("\t")
        if len(cells) > 4:
            country_index[cells[0]] = len(countries)
            countries.append(cells[4])
    regions, region_index, region_names = [], {}, {}
    for line in lines(fetch("admin1CodesASCII.txt", work), None):
        cells = line.rstrip("\n").split("\t")
        if len(cells) > 1:
            region_names[cells[0]] = cells[1]

    def region_of(cc, admin1):
        name = region_names.get(f"{cc}.{admin1}")
        if not name:
            return -1
        if name not in region_index:
            region_index[name] = len(regions)
            regions.append(name)
        return region_index[name]

    rows, towns = [], {}

    def add(cells, kind, km):
        name, ascii_name, cc = cells[1], cells[2], cells[8]
        if cc not in country_index or not name:
            return
        lat, lon = float(cells[4]), float(cells[5])
        if kind:
            # A feature named as a town within ~5 km: the town only.
            if any(abs(lat - a) < 0.05 and abs(lon - b) < 0.05 for a, b in towns.get(fold(name), ())):
                return
        else:
            towns.setdefault(fold(name), []).append((lat, lon))
        spelled = ascii_name if ascii_name and ascii_name.lower() != fold(name) else ""
        rows.append("\t".join([name.replace("\t", " "), spelled, str(round(lat * 1000)), str(round(lon * 1000)),
                               str(kind), str(country_index[cc]), str(region_of(cc, cells[10])), str(km)]))

    for line in lines(fetch("cities500.zip", work), "cities500.txt"):
        cells = line.rstrip("\n").split("\t")
        if len(cells) > 14 and cells[6] == "P":
            add(cells, 0, km_for(int(cells[14] or 0)))
    town_rows = len(rows)
    counts = {}
    for line in lines(fetch("allCountries.zip", work), "allCountries.txt"):
        cells = line.split("\t", 11)
        if len(cells) > 10 and cells[7] in FEATURES:
            counts[cells[7]] = counts.get(cells[7], 0) + 1
            add(cells, KINDS.index(FEATURES[cells[7]]), 4)
    print(f"towns {town_rows}, features {len(rows) - town_rows} of {sum(counts.values())} ({counts})", flush=True)
    return {"made": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "credit": "GeoNames (CC BY 4.0)",
            "countries": countries, "regions": regions, "kinds": KINDS, "rows": "\n".join(rows)}


def upload(path, data):
    """PUT on our server (station-history /tiles/upload, the INGEST_TOKEN secret), with retries."""
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    for attempt in range(4):
        try:
            request = urllib.request.Request(UPLOAD + path, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=300) as reply:
                return reply.read().decode()
        except (urllib.error.URLError, OSError) as error:
            if (isinstance(error, urllib.error.HTTPError) and error.code < 500) or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", help="write places.json here instead of uploading it")
    parser.add_argument("--work", default="places-work", help="where the dumps are kept")
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    data = json.dumps(build(args.work), ensure_ascii=False, separators=(",", ":")).encode()
    print(f"places.json: {len(data) / 1e6:.1f} MB", flush=True)
    if args.local:
        os.makedirs(args.local, exist_ok=True)
        with open(os.path.join(args.local, "places.json"), "wb") as f:
            f.write(data)
    else:
        print(upload("places/v1/places.json", data), flush=True)


if __name__ == "__main__":
    main()
