"""The lakes in our coast (coast_osm.py --lakes FILE; the owner, 6.11.2026, Lake Peipus and the Narva reservoir on the
phone: dark smears on land past the shore, "We have been fixing shoreline shadow for the sea, but I see that we have the
same issue for lakes"). The coast took Natural Earth's 1:10 million lakes, km off the real shore in places, so the map's
colours and their shore shade covered land there. Now the same lakes with HydroLAKES' outlines (Messager et al. 2016,
CC BY 4.0, as our map's lakes): each HydroLAKES lake of 1 km2 or more that overlaps one of Natural Earth's (no new small
lakes, so no shade rings in every lakeland), and Natural Earth's own where HydroLAKES has none.

    python coast_lakes.py lakes.json                 the lakes' file for coast_osm.py --lakes
    python coast_lakes.py --compare 18,9 lakes.json out.png   one zoom-5 tile's water, Natural Earth's lakes beside these

The file: [[[lon, lat], ...] (the outline), [...] (its islands)...] for each lake.
"""
import json
import sys
from pathlib import Path

import numpy as np

import coast_tiles
from water_tiles import LAKES, download, shapefile_in


def chosen():
    """HydroLAKES' outlines of Natural Earth's lakes, as (rings, box), and how many of each."""
    import pyogrio
    import shapely
    work = Path("work")
    work.mkdir(exist_ok=True)
    download(LAKES, work / "lakes.zip")
    natural = coast_tiles.polygons("lakes")
    ne = shapely.make_valid(np.array([shapely.Polygon(r[0], r[1:]) for r, _ in natural], dtype=object))
    _, _, geometry, _ = pyogrio.raw.read(shapefile_in(work / "lakes.zip"), where="Lake_area >= 1", columns=[], read_geometry=True)
    hydro = shapely.make_valid(shapely.from_wkb(geometry))
    pairs = shapely.STRtree(ne).query(hydro, predicate="intersects")
    kept, covered = np.unique(pairs[0]), set(pairs[1].tolist())
    out = []
    for shape in hydro[kept]:
        for part in shapely.get_parts(shapely.get_parts(shape)):
            # A repaired outline may come back with stray lines beside its polygon.
            if part.geom_type != "Polygon":
                continue
            rings = [np.asarray(part.exterior.coords)] + [np.asarray(r.coords) for r in part.interiors]
            out.append(rings)
    # Natural Earth's lakes HydroLAKES doesn't have (none of its lakes meets them): kept as they were.
    out += [natural[i][0] for i in range(len(natural)) if i not in covered]
    print(f"{len(kept)} HydroLAKES lakes over {len(covered)} of Natural Earth's {len(natural)}; "
          f"{len(natural) - len(covered)} of Natural Earth's kept as they were", flush=True)
    return [(rings, box_of(rings[0])) for rings in out]


def box_of(outline):
    return (outline[:, 0].min(), outline[:, 1].min(), outline[:, 0].max(), outline[:, 1].max())


def load(path):
    """The lakes' file as coast_osm.py draws lakes: (rings as lon, lat arrays, the outline's box)."""
    out = []
    for lake in json.loads(Path(path).read_text()):
        rings = [np.asarray(r, dtype=np.float64) for r in lake if len(r) >= 3]
        if rings:
            out.append((rings, box_of(rings[0])))
    return out


def save(lakes, path):
    rounded = [[np.round(r, 5).tolist() for r in rings] for rings, _ in lakes]
    Path(path).write_text(json.dumps(rounded, separators=(",", ":")))
    print(f"{path}: {len(rounded)} lakes, {Path(path).stat().st_size / 1e6:.0f} MB", flush=True)


def compare(tile, path, image):
    """One zoom-5 tile's land at zoom 10 with Natural Earth's lakes (left) and these (right), water dark."""
    import coast_osm
    from PIL import Image
    tx, ty = (int(v) for v in tile.split(","))
    land = coast_osm.load_land(coast_osm.download(Path("land-polygons-split-3857.zip"))).get((tx, ty), [])
    halves = []
    for lakes in (coast_tiles.polygons("lakes"), load(path)):
        result = coast_osm.distance(land, lakes, tx, ty)
        coarse = result[0] if isinstance(result, tuple) else np.full((coast_osm.SPAN, coast_osm.SPAN), 1.0 if result else -1.0)
        halves.append(np.where(coarse > 0, 225, 40).astype(np.uint8))
    Image.fromarray(np.hstack([halves[0], np.full((halves[0].shape[0], 8), 128, np.uint8), halves[1]])).save(image)
    print(f"{image}: Natural Earth's lakes left, HydroLAKES' right", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--compare":
        compare(args[1], args[2], args[3])
    else:
        save(chosen(), args[0])
