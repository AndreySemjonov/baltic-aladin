"""The fine coastline the wind maps use (run once, by hand): a land mask of the regional
map's box at about 250 m, from OpenStreetMap's land polygons (© OpenStreetMap
contributors, ODbL; https://osmdata.openstreetmap.de/data/land-polygons.html, the
"split, WGS84" download). Lakes count as land there, the sea and its bays as sea.

    python build_coast.py land-polygons-split-4326.zip

Writes coast.png (white = land, gray along the shore by how much of the pixel is land: drawn
4 x 4 finer and averaged, so the edge is smooth) and coast.json (its box).
"""
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import shapefile  # pyshp
from PIL import Image, ImageDraw

import maplib
import metnordic_map

HERE = Path(__file__).parent
KM = 0.25
# Drawn this many times finer, then averaged: a soft, smooth shoreline.
SUPER = 4


def main():
    south, north, west, east = metnordic_map.SOUTH, metnordic_map.NORTH, metnordic_map.WEST, metnordic_map.EAST
    (lat, _), width, height = maplib.output_grid(south, north, west, east, KM)
    top, bottom = float(maplib.mercator_y(north)), float(maplib.mercator_y(south))

    def pixel(lon, lat):
        return ((lon - west) / (east - west) * width * SUPER,
                (top - float(maplib.mercator_y(lat))) / (top - bottom) * height * SUPER)

    image = Image.new("L", (width * SUPER, height * SUPER), 0)
    draw = ImageDraw.Draw(image)
    archive = zipfile.ZipFile(sys.argv[1])
    names = {Path(n).suffix: n for n in archive.namelist() if Path(n).stem == "land_polygons"}
    reader = shapefile.Reader(shp=archive.open(names[".shp"]), shx=archive.open(names[".shx"]), dbf=archive.open(names[".dbf"]))
    drawn = 0
    for shape in reader.iterShapes():
        x0, y0, x1, y1 = shape.bbox
        if x1 < west - 0.1 or x0 > east + 0.1 or y1 < south - 0.1 or y0 > north + 0.1:
            continue
        parts = list(shape.parts) + [len(shape.points)]
        for start, end in zip(parts, parts[1:]):
            ring = shape.points[start:end]
            # Outer rings run clockwise in shapefiles, holes the other way.
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
            draw.polygon([pixel(x, y) for x, y in ring], fill=255 if area < 0 else 0)
        drawn += 1
    image = image.resize((width, height), Image.BOX)
    image.save(HERE / "coast.png", optimize=True)
    (HERE / "coast.json").write_text(json.dumps({
        "bounds": {"south": south, "north": north, "west": west, "east": east}, "width": width, "height": height,
        "km": KM, "source": "© OpenStreetMap contributors, ODbL (land polygons, osmdata.openstreetmap.de)"}) + "\n")
    land = np.asarray(image) > 127
    print(f"coast.png {width}x{height}, {drawn} polygons, land {land.mean():.0%}, "
          f"{(HERE / 'coast.png').stat().st_size / 1000:.0f} KB")


if __name__ == "__main__":
    main()
