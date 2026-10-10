"""The rain map's satellite clouds, ready to draw: every 15 minutes of the last day, Meteosat's
infrared picture (MTG FCI, 10.5 um, fine resolution) and the Cloud Mask (MSG) for the rain map's
box, turned into one grey picture with transparency (the owner's style B, 10.10.2026: every cloud a
nearly solid mid-grey with the infrared's streaks in it, only clear sky see-through), and uploaded
to MeteoGust's file store through the station-history Worker (files.meteogust.com/clouds/...).
The app downloads one small picture per time step instead of asking EUMETSAT's map server for
dozens of tiles (slow, and the mask failed on 10-minute times: it has only quarter hours).

    python clouds.py            # the missing pictures of the last day, newest first (at most 16)

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
# The rain map's box (rain/map.json), at twice its grid: about 1.25 km a pixel.
SOUTH, NORTH, WEST, EAST = 53.8, 61.0, 16.5, 28.5
WIDTH, HEIGHT = 576, 644
UPLOAD = os.environ.get("CLOUDS_UPLOAD", "https://data.meteogust.com/clouds/upload/")
INDEX = os.environ.get("CLOUDS_INDEX", "https://files.meteogust.com/clouds/index.json")
PER_RUN = 16
AGENT = "baltic-aladin clouds (+https://github.com/AndreySemjonov/baltic-aladin)"


def mercator(lat, lon):
    r = 6378137.0
    return r * lon * math.pi / 180, r * math.log(math.tan(math.pi / 4 + lat * math.pi / 360))


def picture(layer, when):
    """One WMS picture of the box as a NumPy array (RGB), or None (an error comes back as XML)."""
    x0, y0 = mercator(SOUTH, WEST)
    x1, y1 = mercator(NORTH, EAST)
    query = (f"service=WMS&version=1.3.0&request=GetMap&layers={layer}&styles=&format=image/png&crs=EPSG:3857"
             f"&bbox={x0},{y0},{x1},{y1}&width={WIDTH}&height={HEIGHT}&time={when.strftime('%Y-%m-%dT%H:%M:%SZ')}")
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


def render(infrared, mask_rgb):
    """Style B: the Cloud Mask softened (its pixels are coarser) gives where it is cloudy, nearly
    solid; the infrared's brightness over the clear ground's (the colder, the brighter) the grey
    from 150 to 195 and, where the mask missed, a little cloud. RGBA, straight alpha."""
    grey = infrared.mean(axis=2)
    r, g, b = mask_rgb[..., 0], mask_rgb[..., 1], mask_rgb[..., 2]
    mask = ((r > 200) & (g > 200) & (b > 200) & (np.abs(r - g) < 30) & (np.abs(g - b) < 30)).astype(np.float32)
    clear_pixels = grey[mask < 0.5]
    ground = float(np.median(clear_pixels)) if clear_pixels.size > grey.size / 50 else float(np.percentile(grey, 15))
    soft = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(4)),
                      dtype=np.float32) / 255
    d = grey - ground
    alpha = np.maximum(soft * 0.86, np.clip((d + 2) / 24, 0, 1) * 0.9)
    shade = np.clip(150 + 0.6 * d, 150, 195)
    rgba = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    rgba[..., 0] = shade.astype(np.uint8)
    rgba[..., 1] = np.clip(shade + 2, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(shade + 5, 0, 255).astype(np.uint8)
    rgba[..., 3] = (np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(out, "WEBP", quality=82, method=4)
    return out.getvalue(), ground


def published():
    """The times already in the store (its index), as "YYYYMMDDHHMM" stamps."""
    try:
        request = urllib.request.Request(INDEX + f"?t={int(time.time())}", headers={"User-Agent": AGENT})
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
    have = published()
    missing = [t for t in wanted if t.strftime("%Y%m%d%H%M") not in have][:PER_RUN]
    print(f"Clouds: {len(have)} published, {len(missing)} to make")
    made = 0
    for when in missing:
        # The infrared every 10 minutes: the step at or before the quarter hour; the mask at it.
        infrared = picture(INFRARED, when - timedelta(minutes=when.minute % 10))
        mask = picture(MASK, when)
        if infrared is None or mask is None:
            print(f"  {when:%H:%M}: no picture ({'infrared' if infrared is None else 'mask'})")
            continue
        data, ground = render(infrared, mask)
        stamp = when.strftime("%Y%m%d%H%M")
        request = urllib.request.Request(UPLOAD + stamp, data=data, method="PUT",
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "image/webp",
                                                  "User-Agent": AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as reply:
                reply.read()
            made += 1
            print(f"  {when:%Y-%m-%d %H:%M}: {len(data) // 1024} KB, clear ground grey {ground:.0f}")
        except urllib.error.URLError as error:
            print(f"  {when:%H:%M}: upload failed: {error}")
    print(f"Clouds: {made} made")


if __name__ == "__main__":
    sys.exit(main())
