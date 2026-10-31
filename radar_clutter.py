"""The radar's false echoes (the owner, 14.10.2026: a spot in Masuria rained 10-25 mm/h all day
and all night): places where OPERA's composite shows rain in nearly every image while the land
round them doesn't, ground clutter or a radio link's interference. Read from our own radar tiles
(tiles/radar, zoom 6, about 1.3 km a pixel here) over the last 24 hours, one image every 15
minutes. A spot is a patch of pixels with rain in at least 90 % of the images they were seen in,
at most about 25 km across, whose ring 10 to 25 km out had rain in under 15 % (a front that stays
put is wide: it isn't one); or, very much alone (its ring under 5 %), in 70 %. A pixel station-history
cleared as an echo says so (alpha 254) and counts as rain, so a spot stays found while its echo
goes on (14.10.2026: cleared, it looked dry, dropped off the list and came back); a spot of the
list before stays while it rains in half of the images. Published as tiles/radar/clutter.json ({"updated", "spots": [{lat,
lon, km, share}]}) through station-history, which clears them when it makes the radar's tiles and
checks the rain alerts.

The same over Estonia's own radars (estonia_radar.py's radar-ee files, OPERA's 1 km grid), their 7+ dBZ (the map's
light rain) as rain: the radar Worker draws them only while OPERA lacks Estonia, so their steady echoes hardly show in
the tiles (the owner, 31.10.2026: the Tootsi wind farm, NE of Pärnu, 25-36 dBZ in every image, drawn as spots of rain).

    python radar_clutter.py [--local FILE]
"""
import io
import json
import math
import struct
import sys
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
from PIL import Image
from scipy import ndimage

import world_tiles

BASE = "https://files.meteogust.com/tiles/radar/"
EE = "https://files.meteogust.com/radar-ee/"
# OPERA's grid (estonia_radar.py OPERA): LAEA, 1 km, the top-left corner of pixel (0, 0).
OPERA = "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 +units=m +ellps=WGS84"
Z = 6
SIZE = 256
STEP = 900
SHARE = 0.9
RING = 0.15
# Very much alone: a lower share will do.
ALONE_SHARE = 0.7
ALONE_RING = 0.05
# A spot of the list before stays while it rains (or is cleared) in this share.
KEEP_SHARE = 0.5
MOST_PIXELS = 400
INNER, OUTER = 8, 20


def get(url):
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "baltic-aladin radar_clutter"})
            with urllib.request.urlopen(request, timeout=60) as reply:
                return reply.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5)


def tiles_of(data):
    """A pack's zoom-6 tiles: {(x, y): (rain, seen)} as boolean arrays."""
    length = struct.unpack("<I", data[4:8])[0]
    head = json.loads(data[8:8 + length])
    start = 8 + length
    out = {}
    for key, (offset, count) in head["tiles"].items():
        z, x, y = (int(v) for v in key.split("/"))
        if z != Z:
            continue
        image = np.asarray(Image.open(io.BytesIO(data[start + offset:start + offset + count])).convert("LA"))
        # Rain, or an echo station-history cleared (alpha 254).
        out[(x, y)] = ((image[..., 0] > 0) | (image[..., 1] == 254), image[..., 1] > 0)
    return out


def lonlat(px, py):
    """A zoom-6 pixel's (fractional, over the whole world) longitude and latitude."""
    n = (1 << Z) * SIZE
    lon = px / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / n))))
    return lon, lat


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    index = json.loads(get(BASE + "index.json"))
    frames, last = [], None
    for frame in index["frames"]:
        if last is None or frame["time"] - last >= STEP:
            frames.append(frame)
            last = frame["time"]
    print(f"{len(frames)} images", flush=True)
    rain, seen = {}, {}
    with ThreadPoolExecutor(6) as pool:
        for tiles in pool.map(lambda f: tiles_of(get(BASE + f["file"])), frames):
            for key, (wet, looked) in tiles.items():
                if key not in rain:
                    rain[key] = np.zeros((SIZE, SIZE), np.uint16)
                    seen[key] = np.zeros((SIZE, SIZE), np.uint16)
                rain[key] += wet
                seen[key] += looked
    # One mosaic over the tiles there are.
    xs, ys = [k[0] for k in rain], [k[1] for k in rain]
    x0, y0 = min(xs), min(ys)
    width, height = (max(xs) - x0 + 1) * SIZE, (max(ys) - y0 + 1) * SIZE
    wet = np.zeros((height, width), np.float32)
    looked = np.zeros((height, width), np.float32)
    for (x, y), r in rain.items():
        wet[(y - y0) * SIZE:(y - y0 + 1) * SIZE, (x - x0) * SIZE:(x - x0 + 1) * SIZE] = r
        looked[(y - y0) * SIZE:(y - y0 + 1) * SIZE, (x - x0) * SIZE:(x - x0 + 1) * SIZE] = seen[(x, y)]
    enough = looked >= len(frames) / 2
    share = np.where(enough, wet / np.maximum(looked, 1), 0)

    def mercator(px, py):
        lon, lat = lonlat(px + x0 * SIZE, py + y0 * SIZE)
        return lon, lat, 40_075_016 * math.cos(math.radians(lat)) / ((1 << Z) * SIZE)

    spots = find(share, enough, mercator)
    try:
        for s in estonia([f["time"] for f in frames]):
            if not any(abs(s["lat"] - o["lat"]) < 0.05 and abs(s["lon"] - o["lon"]) < 0.08 for o in spots):
                spots.append(s)
    except Exception as error:
        print(f"Estonia's radars: {error}", flush=True)
    # The list before: its spots stay while they still rain (or are cleared) in half the images.
    try:
        before = json.loads(get(BASE + "clutter.json")).get("spots", [])
    except Exception:
        before = []
    n = (1 << Z) * SIZE
    for old in before:
        if any(abs(s["lat"] - old["lat"]) < 0.05 and abs(s["lon"] - old["lon"]) < 0.08 for s in spots):
            continue
        px = (old["lon"] + 180) / 360 * n - x0 * SIZE
        py = (1 - math.asinh(math.tan(math.radians(old["lat"]))) / math.pi) / 2 * n - y0 * SIZE
        cx, cy = int(px), int(py)
        if not (2 <= cx < width - 2 and 2 <= cy < height - 2):
            continue
        here = share[cy - 2:cy + 3, cx - 2:cx + 3].max()
        if here >= KEEP_SHARE:
            spots.append({**old, "share": round(float(here), 2), "kept": True})
    spots.sort(key=lambda s: -s["share"])
    for s in spots:
        print(f"  {s['lat']:.3f} {s['lon']:.3f}: {s['km']} km, rain {s['share']:.0%}, ring {s['ring']:.0%}", flush=True)
    body = json.dumps({"updated": int(time.time()), "images": len(frames), "spots": spots}, separators=(",", ":")).encode()
    if local:
        with open(local, "wb") as file:
            file.write(body)
    else:
        world_tiles.upload("radar/clutter.json", body)
    print(f"{len(spots)} spots", flush=True)


def estonia(times):
    """Steady echoes of Estonia's radars: their file 5 minutes before each image (as the radar Worker takes them)."""
    from pyproj import Transformer

    def read(t):
        at = datetime.fromtimestamp(t - 300, timezone.utc)
        try:
            raw = zlib.decompress(get(f"{EE}{at:%Y/%m/%d/%H%M}.bin"))
        except Exception:
            return None
        return struct.unpack("<4I", raw[:16]), np.frombuffer(raw[16:], np.uint8)

    wet = looked = grid = None
    count = 0
    with ThreadPoolExecutor(6) as pool:
        # Newest first: their grid is the newest file's (the first files of 30.10 had another).
        for got in pool.map(read, times[::-1]):
            if got is None or (grid and got[0] != grid):
                continue
            grid, values = got
            values = values.reshape(grid[3], grid[2])
            if wet is None:
                wet, looked = np.zeros(values.shape, np.float32), np.zeros(values.shape, np.float32)
            # Echo within 2 km: a wind farm's hops between neighbouring pixels as the blades turn (Tootsi 31.10: 39 %
            # of the images a pixel, 86 % within 2).
            wet += ndimage.maximum_filter(values >= 78, size=5)
            looked += values >= 1
            count += 1
    print(f"Estonia's radars: {count} images", flush=True)
    if count < len(times) / 2:
        return []
    enough = looked >= count / 2
    to_lonlat = Transformer.from_crs(OPERA, "EPSG:4326", always_xy=True)

    def place(px, py):
        lon, lat = to_lonlat.transform(-500 + (grid[0] + px) * 1000, 500 - (grid[1] + py) * 1000)
        return lon, lat, 1000

    return find(np.where(enough, wet / np.maximum(looked, 1), 0), enough, place)


def find(share, enough, place):
    """Spots of a mosaic's share of images with rain (`enough`: looked at in half of them); `place(px, py)` is a
    pixel's longitude, latitude and size in metres."""
    height, width = share.shape
    labels, count = ndimage.label(share >= ALONE_SHARE)
    print(f"{count} patches raining in {ALONE_SHARE:.0%} of the images", flush=True)
    spots = []
    for k, box in enumerate(ndimage.find_objects(labels), start=1):
        patch = labels[box] == k
        pixels = int(patch.sum())
        if pixels > MOST_PIXELS:
            continue
        # The ring round it: 8 to 20 pixels out (about 10 to 25 km).
        sy, sx = box
        top, left = max(sy.start - OUTER, 0), max(sx.start - OUTER, 0)
        bottom, right = min(sy.stop + OUTER, height), min(sx.stop + OUTER, width)
        here = labels[top:bottom, left:right] == k
        near = ndimage.binary_dilation(here, iterations=INNER)
        far = ndimage.binary_dilation(here, iterations=OUTER) & ~near & enough[top:bottom, left:right]
        if far.sum() < 50:
            continue
        ring = float(share[top:bottom, left:right][far].mean())
        mean = float(share[top:bottom, left:right][here].mean())
        if not ((mean >= SHARE and ring < RING) or (mean >= ALONE_SHARE and ring < ALONE_RING)):
            continue
        cy, cx = ndimage.center_of_mass(here)
        lon, lat, metres = place(left + cx + 0.5, top + cy + 0.5)
        # Its size: the patch's radius and a pixel and a half more (about 2 km).
        km = (math.sqrt(pixels / math.pi) + 1.5) * metres / 1000
        spots.append({"lat": round(lat, 4), "lon": round(lon, 4), "km": round(km, 1),
                      "share": round(float(share[top:bottom, left:right][here].mean()), 2), "ring": round(ring, 2)})
    return spots


if __name__ == "__main__":
    main()
