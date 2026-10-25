"""The rain map's satellite clouds, ready to draw: every 15 minutes of the last day, Meteosat's
infrared picture (MTG FCI, 10.5 um, fine resolution) and the Cloud Mask (MSG) for the rain map's
box, turned into one grey picture with transparency (the owner's style B, 10.10.2026: every cloud a
nearly solid mid-grey with the infrared's streaks in it, only clear sky see-through), and uploaded
to MeteoGust's file store through the station-history Worker (files.meteogust.com/clouds/...).
The app downloads one small picture per time step instead of asking EUMETSAT's map server for
dozens of tiles (slow, and the mask failed on 10-minute times: it has only quarter hours).

    python clouds.py            # the missing pictures of the last day, newest first (at most 16)

The rain map's box as one picture (clouds/index.json: the app's builds before 211), and Europe as
map tiles (the owner, 12.10.2026: tiles for everything, fine wherever one looks): a mosaic at zoom
6 (about 1.3 km a pixel here, the satellite's own detail at our latitudes), cut into tiles and
averaged down to zoom 3, one pack per quarter hour (tiles/sat/<stamp>.pack, grey and alpha; tiles
of clear sky left out), the last day listed in tiles/sat/index.json.

Europe's tiles are shaded by the clouds' height (the owner, 14.10.2026: under a low deck Latvia
was one flat grey; the infrared barely tells a low cloud from the ground): EUMETSAT's cloud top
height (MSG, about 3 km) sets the grey, low cloud darker, high cloud brighter, thin high cloud a
little see-through; the texture inside comes by day from the visible channel (MTG FCI 0.6 um,
500 m: the low deck's rolls and cells), at night from the infrared, blended by the sun's height.
Without the height or the visible, the infrared alone as before (style B).

Low water cloud the Cloud Mask misses (the owner, 25.10.2026: "Outside is cloudy but in our app it's sunny"; Riga under
OVC004, 120 m, the mask clear: a deck as warm as the ground, the sun low) is added from EUMETSAT's Fog / Low Clouds RGB
(MTG): green well above red is low water cloud or fog (with_low_cloud).

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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image, ImageFilter

import world_tiles

WMS = "https://view.eumetsat.int/geoserver/wms"
INFRARED = "mtg_fd:ir105_hrfi"
MASK = "msg_fes:clm"
VISIBLE = "mtg_fd:vis06_hrfi"
HEIGHT = "msg_fes:cth"
FOG = "mtg_fd:rgb_fog"
LEGEND = "https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetLegendGraphic&layer=msg_fes:cth&format=image/png"
# The grey by the cloud top's height (km): low darker, high brighter.
HEIGHT_KM = [0, 2, 6, 10, 12]
HEIGHT_GREY = [128, 144, 198, 224, 229]
# The sets: the folder (after clouds/), the box (south, north, west, east), the picture's size, how
# many a run makes, and whether the clear ground's grey is reckoned region by region (Europe's
# ground goes from the Arctic's to the Sahara's: one grey for all made the cold north a cloud).
# The rain map's box (rain/map.json), at twice its grid: about 1.25 km a pixel.
SETS = [
    {"folder": "", "box": (53.8, 61.0, 16.5, 28.5), "size": (576, 644), "per_run": 16, "local": False, "quality": 82, "alpha": 100},
]
# Europe's tiles: zoom 6's columns 27-39 and rows 13-25 (32-72.4N, 28W-45E), zooms 3-6.
TILE_Z, TILE_X, TILE_Y, TILE_ZOOMS = 6, (27, 39), (13, 25), range(3, 7)
# 2 a run with the helper (one is due every quarter hour; a daytime one takes a minute or two); more for a one-off backfill (sat-backfill.yml: the last day at once).
TILES_PER_RUN = int(os.environ.get("TILES_PER_RUN", "2"))
TILES_UPLOAD = os.environ.get("TILES_UPLOAD", "https://data.meteogust.com/tiles/upload/sat/")
TILES_INDEX = "https://files.meteogust.com/tiles/sat/index.json"
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


def with_low_cloud(mask, fog):
    """The Cloud Mask (RGB, cloud white) with the low water cloud it misses: where the Fog / Low Clouds RGB's green is
    well above its red (low water cloud and fog; clear ground and higher, colder cloud are red to blue there), cloudy.
    Checked on four days of the airports' METARs round the Baltic (21-25.10.2026, 1,420 reports, BKN/OVC as cloudy):
    overcast caught at night 74 -> 92 %, at dawn and dusk 69 -> 80 %; of 318 clear night reports 3 called cloud, of
    the dawn and day ones none. By day it adds nothing (the 3.9 um channel's sunlight), and the mask catches 89 %
    there. Without the fog picture (it comes a little later than the infrared) the mask alone."""
    if fog is None or fog.shape != mask.shape:
        return mask
    out = mask.copy()
    out[low_water(fog)] = 255
    return out


def low_water(fog):
    """Where the Fog / Low Clouds RGB shows low water cloud or fog: its green well above its red."""
    return (fog[..., 1] - fog[..., 0] > 40) & (fog[..., 1] > 100)


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
    shade, alpha, ground = style_b(infrared, mask_rgb, local)
    rgba = np.zeros(shade.shape + (4,), dtype=np.uint8)
    rgba[..., 0] = shade.astype(np.uint8)
    rgba[..., 1] = np.clip(shade + 2, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(shade + 5, 0, 255).astype(np.uint8)
    rgba[..., 3] = (np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(out, "WEBP", quality=quality, alpha_quality=alpha_quality, method=4)
    return out.getvalue(), ground


def style_b(infrared, mask_rgb, local=False):
    """The grey (150-195) and how much cloud (0-1) of style B, and the clear ground's grey."""
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
    return shade, np.clip(alpha, 0, 1), float(np.mean(ground))


_legend = None


def height_legend():
    """The cloud top height picture's colours and their metres (its legend's bar: 320 m at x 148
    to 16,000 m at x 630)."""
    global _legend
    if _legend is None:
        request = urllib.request.Request(LEGEND, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=60) as reply:
            bar = np.asarray(Image.open(io.BytesIO(reply.read())).convert("RGB"), dtype=np.float32)[30]
        xs = np.arange(150, 628)
        _legend = (bar[xs], 320 + (xs - 148) / (630 - 148) * (16000 - 320))
    return _legend


def cloud_heights(rgb):
    """The cloud top height (m) of each pixel of its picture; NaN where there's none."""
    colours, metres = height_legend()
    packed = (rgb[..., 0].astype(np.int64) << 16) | (rgb[..., 1].astype(np.int64) << 8) | rgb[..., 2].astype(np.int64)
    unique, inverse = np.unique(packed, return_inverse=True)
    u = np.stack([(unique >> 16) & 255, (unique >> 8) & 255, unique & 255], axis=-1).astype(np.float32)
    d = ((u[:, None, :] - colours[None, :, :]) ** 2).sum(axis=2)
    value = metres[d.argmin(axis=1)].astype(np.float32)
    # No cloud: black or far from every colour of the legend.
    value[(d.min(axis=1) > 900) | (u.sum(axis=1) < 30)] = np.nan
    return value[inverse.reshape(packed.shape)]


def sun_height(lat, lon, when):
    """The sun's height above the horizon (degrees)."""
    day = when.timetuple().tm_yday
    hours = when.hour + when.minute / 60
    decl = math.radians(-23.44) * math.cos(2 * math.pi / 365 * (day + 10))
    angle = np.radians((hours - 12) * 15 + lon)
    phi = np.radians(lat)
    return np.degrees(np.arcsin(np.sin(phi) * math.sin(decl) + np.cos(phi) * math.cos(decl) * np.cos(angle)))


def blur(a, r):
    from scipy import ndimage
    return ndimage.gaussian_filter(np.asarray(a, dtype=np.float32), r)


def smooth_heights(heights, radius):
    """The heights (m, NaN: none) smoothed on their own grid, the gaps filled from round them
    (1.5 km where there's nothing near)."""
    known = np.isfinite(heights)
    weight = blur(known, radius)
    return np.where(weight > 0.05, blur(np.where(known, heights, 0), radius) / np.maximum(weight, 1e-3), 1500).astype(np.float32)


def style_height(infrared, mask_rgb, visible, heights, daylight):
    """The grey and how much cloud, by the cloud top's height (`heights` in m, smooth, every pixel)
    with the texture of the visible (by day, `daylight` 0-1) or the infrared (by night)."""
    _, alpha, _ = style_b(infrared, mask_rgb, local=True)
    km = np.clip(heights / 1000, 0, 12)
    base = np.interp(km, HEIGHT_KM, HEIGHT_GREY)
    ir = infrared.mean(axis=2)
    texture = (ir - blur(ir, 10)) * 2.4
    if visible is not None:
        v = visible.mean(axis=2) / 255
        texture = texture * (1 - daylight) + (v - blur(v, 10)) * 185 * daylight
        # By day the visible's brightness says how thick: dim cloud a little see-through.
        alpha = alpha * (1 - daylight + daylight * np.clip(0.55 + blur(v, 2) * 0.9, 0.55, 1))
    shade = np.clip(base + np.clip(texture, -26, 26), 120, 240)
    # Thin high cloud (a high top, but the infrared hardly colder than the ground): see-through.
    ground = local_ground(ir, (alpha > 0.5).astype(np.float32), float(np.percentile(ir, 15)))
    alpha = np.where((km > 6) & (ir - ground < 18), alpha * 0.6, alpha)
    return shade, np.clip(alpha, 0, 1)


def tile_lat(y, n):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def sat_pack(when):
    """One quarter hour's Europe tiles as a pack (MGT1, LA PNGs), or None without pictures."""
    n = 2 ** TILE_Z
    box = (tile_lat(TILE_Y[1] + 1, n), tile_lat(TILE_Y[0], n), TILE_X[0] / n * 360 - 180, (TILE_X[1] + 1) / n * 360 - 180)
    width, height = (TILE_X[1] - TILE_X[0] + 1) * 256, (TILE_Y[1] - TILE_Y[0] + 1) * 256
    # The four pictures side by side (the visible, 500 m, is slow to come): the infrared and the
    # visible every 10 minutes (the step at or before the quarter hour), the mask and the cloud
    # top height at it; the mask and the height are coarse anyway: a quarter of the pixels.
    lats = np.array([tile_lat(TILE_Y[0] + (j + 0.5) / 16, n) for j in range(16 * (TILE_Y[1] - TILE_Y[0] + 1))])
    lons = np.array([(TILE_X[0] + (i + 0.5) / 16) / n * 360 - 180 for i in range(16 * (TILE_X[1] - TILE_X[0] + 1))])
    daylight = np.clip((sun_height(lats[:, None], lons[None, :], when) - 3) / 10, 0, 1).astype(np.float32)
    ten = when - timedelta(minutes=when.minute % 10)
    jobs = {"infrared": (INFRARED, ten, (width, height)), "mask": (MASK, when, (width // 4, height // 4)),
            "height": (HEIGHT, when, (width // 4, height // 4)), "fog": (FOG, ten, (width // 4, height // 4))}
    if daylight.max() > 0:
        jobs["visible"] = (VISIBLE, ten, (width, height))
    with ThreadPoolExecutor(len(jobs)) as pool:
        got = dict(zip(jobs, pool.map(lambda job: picture(job[0], job[1], box, job[2]), jobs.values())))
    infrared, mask, cth, visible = got["infrared"], got["mask"], got["height"], got.get("visible")
    if infrared is None or mask is None:
        return None
    mask = with_low_cloud(mask, got.get("fog"))
    # The morning: as the sun comes up the fog picture loses the low deck (its 3.9 um channel sees sunlight; Riga
    # 25.10.2026, OVC004 all morning: seen until 05:20 UTC, lost from 05:50) and the mask is still blind to it. Where
    # the sun is rising and under 12 deg, the fog pictures of the last two hours count too, each where the sun was
    # still below the horizon when it was taken (four days' morning METARs: 40 of the 47 overcast reports still missed
    # caught, 5 clear reports of 72 called cloud, decks that broke up after sunrise; looking back one hour: 25 and 1).
    def grid(a):
        return np.asarray(Image.fromarray(a.astype(np.uint8) * 255).resize((mask.shape[1], mask.shape[0]), Image.NEAREST)) > 127

    sun_now = sun_height(lats[:, None], lons[None, :], when)
    rising = (sun_now >= -6) & (sun_now < 12) & (sun_now > sun_height(lats[:, None], lons[None, :], when - timedelta(minutes=30)))
    if rising.any():
        for minutes in (30, 60, 90, 120):
            then = ten - timedelta(minutes=minutes)
            dark = rising & (sun_height(lats[:, None], lons[None, :], then) < 0)
            if not dark.any():
                continue
            earlier = picture(FOG, then, box, (width // 4, height // 4))
            if earlier is not None and earlier.shape == mask.shape:
                mask[low_water(earlier) & grid(dark)] = 255
    mask = np.asarray(Image.fromarray(mask.astype(np.uint8)).resize((width, height), Image.NEAREST), dtype=np.float32)
    try:
        heights = None if cth is None else cloud_heights(cth)
    except (urllib.error.URLError, OSError, ValueError):
        heights = None
    if heights is None:
        shade, alpha, _ = style_b(infrared, mask, local=True)
    else:
        # Smoothed on its own 3 km grid, then spread out smoothly (no blocks, no blobs).
        heights = smooth_heights(heights, 1.5)
        heights = np.asarray(Image.fromarray(heights).resize((width, height), Image.BILINEAR), dtype=np.float32)
        heights = blur(heights, 3)
        daylight = np.asarray(Image.fromarray(daylight).resize((width, height), Image.BILINEAR), dtype=np.float32)
        shade, alpha = style_height(infrared, mask, visible, heights, daylight)
    tiles = {}
    for z in TILE_ZOOMS:
        f = 2 ** (TILE_Z - z)
        for ty in range(TILE_Y[0] // f, TILE_Y[1] // f + 1):
            for tx in range(TILE_X[0] // f, TILE_X[1] // f + 1):
                # The tile's part of the mosaic (zoom 6 pixels), the rest clear sky.
                x0, y0 = tx * f * 256 - TILE_X[0] * 256, ty * f * 256 - TILE_Y[0] * 256
                size = 256 * f
                a = np.zeros((size, size), dtype=np.float32)
                g = np.zeros((size, size), dtype=np.float32)
                cx0, cy0 = max(x0, 0), max(y0, 0)
                cx1, cy1 = min(x0 + size, width), min(y0 + size, height)
                if cx1 <= cx0 or cy1 <= cy0:
                    continue
                a[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = alpha[cy0:cy1, cx0:cx1]
                g[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] = shade[cy0:cy1, cx0:cx1] * alpha[cy0:cy1, cx0:cx1]
                if f > 1:
                    a = a.reshape(256, f, 256, f).mean(axis=(1, 3))
                    g = g.reshape(256, f, 256, f).mean(axis=(1, 3))
                if a.max() < 0.02:
                    continue
                grey = np.where(a > 0.001, g / np.maximum(a, 0.001), 170)
                # The grey in steps of 3 and the cloud in steps of 8: the same to the eye, 40 % smaller.
                grey = np.rint((grey - 150) / 3) * 3 + 150
                cover = np.rint(a * 255 / 8) * 8
                la = np.stack([np.clip(grey, 0, 255), np.clip(cover, 0, 255)], axis=-1).astype(np.uint8)
                tiles[(z, tx, ty)] = world_tiles.png(la, "LA")
    return world_tiles.encode(tiles, {z: 256 for z in TILE_ZOOMS})


def sat_published():
    """The quarter hours already in the tiles' index, as stamps."""
    try:
        request = urllib.request.Request(TILES_INDEX + f"?t={int(time.time())}", headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=30) as reply:
            return {f["stamp"] for f in json.load(reply).get("frames", [])}
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return set()


def make_tiles(wanted, token):
    """Europe's missing tile packs, newest first."""
    have = sat_published()
    missing = [t for t in wanted if t.strftime("%Y%m%d%H%M") not in have][:TILES_PER_RUN]
    print(f"Satellite tiles: {len(have)} published, {len(missing)} to make")
    for when in missing:
        started = time.time()
        pack = sat_pack(when)
        if pack is None:
            print(f"  {when:%H:%M}: no picture")
            continue
        stamp = when.strftime("%Y%m%d%H%M")
        request = urllib.request.Request(TILES_UPLOAD + stamp + ".pack", data=pack, method="PUT",
                                         headers={"Authorization": "Bearer " + token, "Content-Type": "application/octet-stream",
                                                  "User-Agent": AGENT})
        try:
            with urllib.request.urlopen(request, timeout=120) as reply:
                reply.read()
            print(f"  {when:%Y-%m-%d %H:%M}: {len(pack) // 1024} KB, {time.time() - started:.0f} s")
        except urllib.error.URLError as error:
            print(f"  {when:%H:%M}: upload failed: {error}")


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
    make_tiles(wanted, token)


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
        mask = with_low_cloud(mask, picture(FOG, when - timedelta(minutes=when.minute % 10), one["box"], one["size"]))
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
