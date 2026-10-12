"""The world's rivers and lakes as map tiles (the owner, 12.10.2026: away from the Baltic the map
had only the base map's few big rivers, at home our own OpenStreetMap lines, build_lines.py).

From HydroSHEDS (WWF): HydroRIVERS (Lehner & Grill 2013; free for scientific, educational and
commercial use, HydroSHEDS licence) and HydroLAKES (Messager et al. 2016, CC BY 4.0). The rivers
from 50 km2 of catchment up, the lakes from 10 ha, each from the zoom where it matters, as on the
Baltic's lines: the big ones always, more from the middle zoom, the rest close in. What lies
wholly inside our Baltic lines' box is left out (ours are finer there).

Made once (by hand, water.yml): GDAL reads the data, tippecanoe makes vector tiles (zooms 0-10,
layers "rivers" with d = detail 0-2 and "lakes"), packed as the weather tiles ("MGT1": header
length, JSON header {"tiles": {"z/x/y": [offset from the header's end, length]}}, the tiles):
low.pack for zooms 0-4, 5-x-y.pack for each zoom-5 tile with water (its zooms 5-10). Uploaded
through station-history as tiles/water/, which serves them as /water/z/x/y.pbf.

    python water_tiles.py                    download, make and upload (INGEST_TOKEN)
    python water_tiles.py --local DIR        write the packs to DIR instead
    python water_tiles.py --work DIR         working folder (default ./water-work)
"""
import gzip
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

RIVERS = "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_shp.zip"
LAKES = "https://data.hydrosheds.org/file/hydrolakes/HydroLAKES_polys_v10_shp.zip"
USER_AGENT = "MeteoGust water tiles (one-time build; https://github.com/AndreySemjonov/baltic-aladin)"
# Our Baltic lines' box (build_lines.py: metnordic_map's box and its 0.3 degree margin).
BALTIC = (53.5, 61.3, 16.2, 28.8)
TOP = 10


def download(url, target):
    if target.exists() and target.stat().st_size > 1_000_000:
        return
    started = time.time()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=600) as reply, open(target.with_suffix(".part"), "wb") as out:
        while chunk := reply.read(1 << 22):
            out.write(chunk)
    target.with_suffix(".part").rename(target)
    print(f"{target.name}: {target.stat().st_size / 1e6:.0f} MB in {time.time() - started:.0f} s", flush=True)


def shapefile_in(zip_path):
    """The shapefile's path inside the zip, for GDAL (/vsizip/)."""
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".shp")]
    if len(names) != 1:
        raise SystemExit(f"{zip_path.name}: shapefiles {names}")
    return f"/vsizip/{zip_path.resolve().as_posix()}/{names[0]}"


def ogr(source, fields, where, target):
    """The features as GeoJSON lines (only the fields asked for)."""
    if target.exists() and target.stat().st_size > 1_000_000:
        return
    started = time.time()
    subprocess.run(["ogr2ogr", "-f", "GeoJSONSeq", str(target), source, "-select", ",".join(fields), "-where", where,
                    "-lco", "COORDINATE_PRECISION=5"], check=True)
    print(f"{target.name}: {target.stat().st_size / 1e6:.0f} MB in {time.time() - started:.0f} s", flush=True)


def baltic_only(coordinates):
    """Whether the whole shape lies inside our Baltic lines' box."""
    points = np.asarray(coordinates, dtype=np.float64).reshape(-1, 2)
    s, n, w, e = BALTIC
    return bool(points[:, 1].min() >= s and points[:, 1].max() <= n and points[:, 0].min() >= w and points[:, 0].max() <= e)


def chaikin(line, rounds=2):
    """The river smoothed (its 15 arc-second steps rounded off), its ends kept where they join."""
    p = np.asarray(line, dtype=np.float64)
    for _ in range(rounds):
        if len(p) < 3:
            break
        q = 0.75 * p[:-1] + 0.25 * p[1:]
        r = 0.25 * p[:-1] + 0.75 * p[1:]
        middle = np.empty((2 * len(q), 2))
        middle[0::2], middle[1::2] = q, r
        p = np.vstack([p[:1], middle[1:-1], p[-1:]])
    return np.round(p, 5).tolist()


def river_zoom(upland):
    """(min zoom, detail): the big rivers always, more from the middle zoom, the rest close in."""
    if upland >= 100_000:
        return 0, 0
    if upland >= 20_000:
        return 2, 0
    if upland >= 2_000:
        return 3, 0
    if upland >= 300:
        return 5, 1
    return 7, 2


def lake_zoom(area):
    for limit, zoom in ((1000, 0), (100, 2), (10, 3), (1, 5), (0.3, 6)):
        if area >= limit:
            return zoom
    return 7


def prepare(raw, target, kind):
    """The tippecanoe input: each feature with its zoom (and the rivers smoothed), the Baltic's out."""
    if target.exists() and target.stat().st_size > 1_000_000:
        return
    started, kept, dropped = time.time(), 0, 0
    with open(raw, encoding="utf-8") as source, open(target, "w", encoding="utf-8") as out:
        for line in source:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            feature = json.loads(line)
            geometry, properties = feature.get("geometry"), feature.get("properties") or {}
            if not geometry:
                continue
            if kind == "rivers":
                if geometry["type"] == "LineString":
                    lines = [geometry["coordinates"]]
                else:
                    lines = geometry["coordinates"]
                if baltic_only([p for l in lines for p in l]):
                    dropped += 1
                    continue
                zoom, detail = river_zoom(float(properties.get("UPLAND_SKM") or 0))
                smooth = [chaikin(l) for l in lines if len(l) >= 2]
                geometry = {"type": "LineString", "coordinates": smooth[0]} if len(smooth) == 1 else \
                    {"type": "MultiLineString", "coordinates": smooth}
                properties = {"d": detail}
            else:
                rings = geometry["coordinates"][0] if geometry["type"] == "Polygon" else geometry["coordinates"][0][0]
                if baltic_only(rings):
                    dropped += 1
                    continue
                zoom = lake_zoom(float(properties.get("Lake_area") or 0))
                properties = {}
            out.write(json.dumps({"type": "Feature", "tippecanoe": {"minzoom": zoom}, "properties": properties,
                                  "geometry": geometry}, separators=(",", ":")) + "\n")
            kept += 1
    print(f"{target.name}: {kept} kept, {dropped} in the Baltic box, {time.time() - started:.0f} s", flush=True)


def tiles(rivers, lakes, target):
    if target.exists():
        target.unlink()
    started = time.time()
    subprocess.run(["tippecanoe", "-o", str(target), "-Z0", f"-z{TOP}", "--force", "--quiet", "-P",
                    "--no-tile-compression", "--no-tile-size-limit", "--no-feature-limit",
                    "-L", f"rivers:{rivers}", "-L", f"lakes:{lakes}"], check=True)
    print(f"{target.name}: {target.stat().st_size / 1e6:.0f} MB in {time.time() - started:.0f} s", flush=True)


def pack(tiles):
    header, offset, body = {}, 0, []
    for key in sorted(tiles):
        data = tiles[key]
        header["%d/%d/%d" % key] = [offset, len(data)]
        body.append(data)
        offset += len(data)
    zooms = [k[0] for k in tiles]
    head = json.dumps({"sizes": {}, "zooms": [min(zooms), max(zooms)], "tiles": header}, separators=(",", ":")).encode()
    import struct
    return b"MGT1" + struct.pack("<I", len(head)) + head + b"".join(body)


def main():
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    work = Path(args[args.index("--work") + 1]) if "--work" in args else Path("water-work")
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()

    download(RIVERS, work / "rivers.zip")
    download(LAKES, work / "lakes.zip")
    ogr(shapefile_in(work / "rivers.zip"), ["UPLAND_SKM"], "UPLAND_SKM >= 50", work / "rivers-raw.geojsons")
    ogr(shapefile_in(work / "lakes.zip"), ["Lake_area"], "Lake_area >= 0.1", work / "lakes-raw.geojsons")
    prepare(work / "rivers-raw.geojsons", work / "rivers.geojsons", "rivers")
    prepare(work / "lakes-raw.geojsons", work / "lakes.geojsons", "lakes")
    for name in ("rivers.zip", "lakes.zip", "rivers-raw.geojsons", "lakes-raw.geojsons"):
        (work / name).unlink(missing_ok=True)
    mbtiles = work / "water.mbtiles"
    tiles(work / "rivers.geojsons", work / "lakes.geojsons", mbtiles)

    # The tiles by pack (MBTiles counts rows from the south).
    groups = defaultdict(dict)
    with sqlite3.connect(mbtiles) as db:
        for z, x, row, data in db.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"):
            y = (1 << z) - 1 - row
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            name = "low" if z <= 4 else f"5-{x >> (z - 5)}-{y >> (z - 5)}"
            groups[name][(z, x, y)] = bytes(data)
    print(f"{sum(len(g) for g in groups.values())} tiles in {len(groups)} packs", flush=True)

    if not local:
        import world_tiles
    total, biggest = 0, (0, "")
    for name in sorted(groups):
        data = pack(groups[name])
        total += len(data)
        biggest = max(biggest, (len(data), name))
        if local:
            local.mkdir(parents=True, exist_ok=True)
            (local / f"{name}.pack").write_bytes(data)
        else:
            world_tiles.upload(f"water/{name}.pack", data)
    index = {"source": "HydroSHEDS (WWF): HydroRIVERS (Lehner & Grill 2013), HydroLAKES (Messager et al. 2016, CC BY 4.0)",
             "zooms": [0, TOP], "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "packs": sorted(groups)}
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (local / "index.json").write_bytes(body)
    else:
        print(world_tiles.upload("water/index.json", body).strip())
    print(f"water: {len(groups)} packs, {total / 1e6:.0f} MB (biggest {biggest[1]}, {biggest[0] / 1e6:.1f} MB), "
          f"{time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
