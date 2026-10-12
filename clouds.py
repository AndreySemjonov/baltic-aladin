"""The rain map's satellite clouds, ready to draw: every 15 minutes of the last day, Meteosat's
infrared picture (MTG FCI, 10.5 um, fine resolution) and the Cloud Mask (MSG) for the rain map's
box, turned into one grey picture with transparency (the owner's style B, 10.10.2026: every cloud a
nearly solid mid-grey with the infrared's streaks in it, only clear sky see-through), and uploaded
to MeteoGust's file store through the station-history Worker (files.meteogust.com/clouds/...).
The app downloads one small picture per time step instead of asking EUMETSAT's map server for
dozens of tiles (slow, and the mask failed on 10-minute times: it has only quarter hours).

    python clouds.py            # the missing pictures of the last day, newest first (at most 16)

Two sets: the rain map's box, fine (about 1.25 km a pixel), and Europe under it, coarser (about
3.5 km at 50N; the owner, 12.10.2026: a grey rectangle round the Baltic and nothing elsewhere),
each with its own index (clouds/index.json, clouds/eu/index.json).

Needs INGEST_TOKEN (the helper's report token) in the environment; without it nothing is sent.
EUMETSAT's open data (EUMETView WMS); credit: "Meteosat, EUMETSAT".
"""
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image, ImageFilter

WMS = "https://view.eumetsat.int/geoserver/wms"
INFRARED = "mtg_fd:ir105_hrfi"
MASK = "msg_fes:clm"
# The sets: the folder (after clouds/), the box (south, north, west, east), the picture's size, how
# many a run makes, and whether the clear ground's grey is reckoned region by region (Europe's
# ground goes from the Arctic's to the Sahara's: one grey for all made the cold north a cloud).
# The rain map's box (rain/map.json), at twice its grid: about 1.25 km a pixel.
SETS = [
    {"folder": "", "box": (53.8, 61.0, 16.5, 28.5), "size": (576, 644), "per_run": 16, "local": False, "quality": 82, "alpha": 100},
    {"folder": "eu/", "box": (34.0, 71.5, -25.0, 45.0), "size": (1200, 1164), "per_run": 6, "local": True, "quality": 70, "alpha": 60},
]
UPLOAD = os.environ.get("CLOUDS_UPLOAD", "https://data.meteogust.com/clouds/upload/")
FILES = "https://files.meteogust.com/clouds/"
AGENT = "baltic-aladin clouds (+https://github.com/AndreySemjonov/baltic-aladin)"


def mercator(lat, lon):
    r = 6378137.0
    return r * lon * math.pi / 180, r * math.log(math.tan(math.pi / 4 + lat * math.pi / 360))


def picture(layer, when, box, size):
    """One WMS picture of the box as a NumPy array (RGB), or None (an error comes back as XML)."""
    south, north, west, east = box
    x0, y0 = mercator(south, west)
    x1, y1 = mercator(north, east)
    query = (f"service=WMS&version=1.3.0&request=GetMap&layers={layer}&styles=&format=image/png&crs=EPSG:3857"
             f"&bbox={x0},{y0},{x1},{y1}&width={size[0]}&height={size[1]}&time={when.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    for attempt in range(3):
        try:
            request = urllib.request.Request(WMS + "?" + query, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=60) as reply:
                data = reply.read()
            if data[:4] != b"\x89PNG":
                return None
            return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)
        except (urllib.error.URLError, OSError):
            time.sleep(3 * (attempt + 1))
    return None


def local_ground(grey, mask, ground, block=64):
    """The clear ground's grey region by region: the median of the clear pixels in each block (the
    whole picture's where a block has too few), smoothed out to every pixel."""
    rows, cols = -(-grey.shape[0] // block), -(-grey.shape[1] // block)
    grid = np.full((rows, cols), ground, dtype=np.float32)
    for j in range(rows):
        for i in range(cols):
            g = grey[j * block:(j + 1) * block, i * block:(i + 1) * block]
            clear = g[mask[j * block:(j + 1) * block, i * block:(i + 1) * block] < 0.5]
            if clear.size > g.size / 10:
                grid[j, i] = np.median(clear)
    padded = np.pad(grid, 1, mode="edge")
    grid = sum(padded[dj:dj + rows, di:di + cols] for dj in range(3) for di in range(3)) / 9
    small = Image.fromarray(grid.astype(np.float32), "F")
    return np.asarray(small.resize((grey.shape[1], grey.shape[0]), Image.BILINEAR), dtype=np.float32)


def render(infrared, mask_rgb, local=False, quality=82, alpha_quality=100):
    """Style B: the Cloud Mask softened (its pixels are coarser) gives where it is cloudy, nearly
    solid; the infrared's brightness over the clear ground's (the colder, the brighter) the grey
    from 150 to 195 and, where the mask missed, a little cloud. RGBA, straight alpha."""
    grey = infrared.mean(axis=2)
    r, g, b = mask_rgb[..., 0], mask_rgb[..., 1], mask_rgb[..., 2]
    mask = ((r > 200) & (g > 200) & (b > 200) & (np.abs(r - g) < 30) & (np.abs(g - b) < 30)).astype(np.float32)
    clear_pixels = grey[mask < 0.5]
    ground = float(np.median(clear_pixels)) if clear_pixels.size > grey.size / 50 else float(np.percentile(grey, 15))
    if local:
        ground = local_ground(grey, mask, ground)
    soft = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(4)),
                      dtype=np.float32) / 255
    d = grey - ground
    alpha = np.maximum(soft * 0.86, np.clip((d + 2) / 24, 0, 1) * 0.9)
    shade = np.clip(150 + 0.6 * d, 150, 195)
    rgba = np.zeros(grey.shape + (4,), dtype=np.uint8)
    rgba[..., 0] = shade.astype(np.uint8)
    rgba[..., 1] = np.clip(shade + 2, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(shade + 5, 0, 255).astype(np.uint8)
    rgba[..., 3] = (np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(out, "WEBP", quality=quality, alpha_quality=alpha_quality, method=4)
    return out.getvalue(), float(np.mean(ground))


def published(folder):
    """The times already in the set's store (its index), as "YYYYMMDDHHMM" stamps."""
    try:
        request = urllib.request.Request(FILES + folder + f"index.json?t={int(time.time())}", headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=30) as reply:
            return {f["stamp"] for f in json.load(reply).get("frames", [])}
    except (urllib.error.URLError, OSError, ValueError):
        return set()


def main():
    token = os.environ.get("INGEST_TOKEN", "")
    if not token:
        print("No INGEST_TOKEN: no clouds sent")
        return
    now = datetime.now(timezone.utc)
    # Quarter hours of the last day, at least 30 minutes old (EUMETSAT's newest come late).
    newest = (now - timedelta(minutes=30)).replace(second=0, microsecond=0)
    newest -= timedelta(minutes=newest.minute % 15)
    wanted = [newest - timedelta(minutes=15 * k) for k in range(96)]
    for one in SETS:
        make(one, wanted, token)


def make(one, wanted, token):
    """One set's missing pictures, newest first."""
    name = one["folder"].rstrip("/") or "baltic"
    have = published(one["folder"])
    missing = [t for t in wanted if t.strftime("%Y%m%d%H%M") not in have][:one["per_run"]]
    print(f"Clouds ({name}): {len(have)} published, {len(missing)} to make")
    made = 0
    for when in missing:
        # The infrared every 10 minutes: the step at or before the quarter hour; the mask at it.
        infrared = picture(INFRARED, when - timedelta(minutes=when.minute % 10), one["box"], one["size"])
        mask = picture(MASK, when, one["box"], one["size"])
        if infrared is None or mask is None:
            print(f"  {when:%H:%M}: no picture ({'infrared' if infrared is None else 'mask'})")
            continue
        data, ground = render(infrared, mask, one["local"], one["quality"], one["alpha"])
        stamp = when.strftime("%Y%m%d%H%M")
        request = urllib.request.Request(UPLOAD + one["folder"] + stamp, data=data, method="PUT",
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "image/webp",
                                                  "User-Agent": AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as reply:
                reply.read()
            made += 1
            print(f"  {when:%Y-%m-%d %H:%M}: {len(data) // 1024} KB, clear ground grey {ground:.0f}")
        except urllib.error.URLError as error:
            print(f"  {when:%H:%M}: upload failed: {error}")
    print(f"Clouds ({name}): {made} made")


if __name__ == "__main__":
    sys.exit(main())
