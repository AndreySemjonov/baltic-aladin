"""The rain map's satellite clouds, ready to draw: every 15 minutes of the last day, Meteosat's
infrared picture (MTG FCI, 10.5 um, fine resolution) and the Cloud Mask (MSG) for the rain map's
box, turned into one grey picture with transparency (the owner's style B, 10.10.2026: every cloud a
nearly solid mid-grey with the infrared's streaks in it, only clear sky see-through), and uploaded
to MeteoGust's file store through the station-history Worker (files.meteogust.com/clouds/...).
The app downloads one small picture per time step instead of asking EUMETSAT's map server for
dozens of tiles (slow, and the mask failed on 10-minute times: it has only quarter hours).

    python clouds.py            # the missing pictures of the last day, newest first (at most 16)
    python clouds.py --local DIR   # written under DIR instead (clouds/..., tiles/sat/...), nothing uploaded
    python clouds.py --remake FROM TO   # the tile packs from FROM to TO (YYYYMMDDHHMM, UTC) made again

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
import http.client
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
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
            rgba = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
            if blank(layer, rgba):
                return None
            return rgba[..., :3].astype(np.float32)
        # A reply cut off half way (http.client.IncompleteRead, not an OSError: EUMETSAT's slow afternoon, 26.10.2026)
        # tried again too, not the whole quarter hour lost.
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            time.sleep(3 * (attempt + 1))
    return None


def blank(layer, rgba):
    """A picture with nothing in it: EUMETSAT's map server answers a time whose MTG pictures are missing with a PNG
    all the same (28.10.2026 02:50-03:20 and 07:10 UTC: the infrared one flat white, the visible and the Fog / Low
    Clouds fully see-through; the 03:00 frame made from them was nearly clear over all Europe, the fog picture's black
    read as thin cloud, and by day the visible's as cloud no brighter than the ground, the owner's screenshots: "at
    5:00 clouds disappears completely"). Mostly see-through, or one colour, is no picture; the cloud top's height is
    see-through over clear sky, and the mask may be one white over an overcast box."""
    if layer == HEIGHT:
        return False
    if (rgba[..., 3] > 127).mean() < 0.5:
        return True
    return layer != MASK and int(np.ptp(rgba[..., :3])) < 3


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


def style_height(infrared, mask_rgb, visible, heights, daylight, fog=None, sun=None):
    """The grey and how much cloud, by the cloud top's height (`heights` in m, smooth, every pixel)
    with the texture of the visible (by day, `daylight` 0-1) or the infrared (by night); how thick by night from
    the Fog / Low Clouds RGB (`fog`, the pictures' size) when there is one; `sun`: the sun's height (deg) per pixel."""
    _, alpha, _ = style_b(infrared, mask_rgb, local=True)
    km = np.clip(heights / 1000, 0, 12)
    base = np.interp(km, HEIGHT_KM, HEIGHT_GREY)
    ir = infrared.mean(axis=2)
    texture = (ir - blur(ir, 10)) * 2.4
    if visible is not None:
        v = visible.mean(axis=2) / 255
        texture = texture * (1 - daylight) + (v - blur(v, 10)) * 185 * daylight
        # By day how thick follows how much brighter than the clear ground round it the visible is (the owner's photos,
        # 25.10.2026: Roja blue sky under thin cirrus and Bērzciems' cumulus gaps drawn as one grey sheet). The mask
        # (about 3 km) calls thin cirrus and a square with some cumulus cloudy alike; the visible (500 m) shows the
        # ground through them (10:00 UTC: Roja 51-58 of 255 under cirrus, clear land 52; the decks round the gulf
        # 97-115). Four days of daytime METARs, the mask cloudy: overcast +37 over the ground (median, a quarter under
        # +21), the mask's cloud at clear reports +16 (a third under +5). +4 and less nearly see-through (0.15), +22
        # and more solid.
        ground_v = local_ground(v, (alpha > 0.5).astype(np.float32),
                                float(np.percentile(v[alpha < 0.5], 50)) if (alpha < 0.5).sum() > v.size / 50 else float(np.percentile(v, 20)))
        # With the sun low everything in the visible is darker, a deck's lead over the ground too: the thresholds scaled
        # by sin(sun) / sin(20 deg), at least half (28.10.2026, seven days' daytime METARs where we draw cloud: overcast
        # drawn see-through 37 of 848 -> 15, clear drawn solid 146 of 219 -> 158; 15 deg as the reference only 29).
        scale = 1.0 if sun is None else np.clip(np.sin(np.radians(sun)) / math.sin(math.radians(20)), 0.5, 1)
        thick = np.clip((blur(v, 1) - ground_v - 4 / 255 * scale) / (18 / 255 * scale), 0, 1)
        alpha = alpha * (1 - daylight + daylight * (0.15 + 0.85 * thick))
    shade = np.clip(base + np.clip(texture, -26, 26), 120, 240)
    if fog is not None and fog.shape[:2] == alpha.shape:
        # By night how thick follows the split window (the owner, 26.10.2026: "We can make A but if this will not brake
        # resolution on day"): the Fog / Low Clouds RGB's red is IR12.0 - IR10.8, strongly negative (dark) through thin
        # cirrus, near 0 (bright) in thick cloud, by day and night alike. Tuned on the daytime pictures of 23-24.10, the
        # mask's cloud judged by the visible: thin cloud red 19-30 (median), thick 108-129; high cloud red < 60 caught
        # 92 % of the thin, thinned 18 % of the thick. 15 and less nearly see-through (0.15), 70 and more solid; low water
        # cloud and fog (with_low_cloud's deck) solid. Only where the sun is down (1 - daylight): the day as it was. The
        # grey (the cloud top's height) as it was.
        red = fog[..., 0].astype(np.float32)
        night_thick = np.where(low_water(fog), 1.0, np.clip((blur(red, 1) - 15) / 55, 0, 1))
        alpha = alpha * (daylight + (1 - daylight) * (0.15 + 0.85 * night_thick))
    else:
        # Without the fog picture: thin high cloud (a high top, the infrared hardly colder than the ground) see-through.
        ground = local_ground(ir, (alpha > 0.5).astype(np.float32), float(np.percentile(ir, 15)))
        alpha = np.where((km > 6) & (ir - ground < 18), alpha * 0.6, alpha)
    return shade, np.clip(alpha, 0, 1)


def tile_lat(y, n):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def sat_box():
    """Europe's tiles' box (south, north, west, east), the mosaic's size and the latitudes and longitudes of its
    daylight grid (16 a tile)."""
    n = 2 ** TILE_Z
    box = (tile_lat(TILE_Y[1] + 1, n), tile_lat(TILE_Y[0], n), TILE_X[0] / n * 360 - 180, (TILE_X[1] + 1) / n * 360 - 180)
    width, height = (TILE_X[1] - TILE_X[0] + 1) * 256, (TILE_Y[1] - TILE_Y[0] + 1) * 256
    lats = np.array([tile_lat(TILE_Y[0] + (j + 0.5) / 16, n) for j in range(16 * (TILE_Y[1] - TILE_Y[0] + 1))])
    lons = np.array([(TILE_X[0] + (i + 0.5) / 16) / n * 360 - 180 for i in range(16 * (TILE_X[1] - TILE_X[0] + 1))])
    return box, width, height, lats, lons


def sat_pack(when, incomplete=True):
    """One quarter hour's Europe tiles as a pack (MGT1, LA PNGs) and how it was made: (pack, "complete"),
    (pack, "without the visible" / "the fog picture") or (None, why). The pictures that say how thick (by day the
    visible, by night the Fog / Low Clouds RGB; big and slow at EUMETSAT, they fail now and then) missing, the ones
    10 minutes before; without those either, made only when `incomplete` (the owner's video, 26.10.2026: a frame made
    without the visible was the mask's solid sheet between thinned ones, and by day the clouds came and went sliding
    the map; "build A, with B's remake only as the net")."""
    box, width, height, lats, lons = sat_box()
    # The four pictures side by side (the visible, 500 m, is slow to come): the infrared and the
    # visible every 10 minutes (the step at or before the quarter hour), the mask and the cloud
    # top height at it; the mask and the height are coarse anyway: a quarter of the pixels.
    daylight = np.clip((sun_height(lats[:, None], lons[None, :], when) - 3) / 10, 0, 1).astype(np.float32)
    ten = when - timedelta(minutes=when.minute % 10)
    jobs = {"infrared": (INFRARED, ten, (width, height)), "mask": (MASK, when, (width // 4, height // 4)),
            "height": (HEIGHT, when, (width // 4, height // 4)), "fog": (FOG, ten, (width // 4, height // 4))}
    if daylight.max() > 0:
        jobs["visible"] = (VISIBLE, ten, (width, height))
    with ThreadPoolExecutor(len(jobs)) as pool:
        got = dict(zip(jobs, pool.map(lambda job: picture(job[0], job[1], box, job[2]), jobs.values())))
    infrared, mask, cth, visible = got["infrared"], got["mask"], got["height"], got.get("visible")
    # The infrared missing (or a blank), the one 10 or 20 minutes before: it gives the texture only, the mask and the
    # height where and how high (28.10.2026: MTG's 02:50 and 03:00 blank, 02:40 fine).
    for minutes in (10, 20):
        if infrared is None and mask is not None:
            infrared = picture(INFRARED, ten - timedelta(minutes=minutes), box, (width, height))
            if infrared is not None:
                print(f"  {when:%H:%M}: the infrared of {ten - timedelta(minutes=minutes):%H:%M}")
    if infrared is None or mask is None:
        return None, "no picture"
    # By night the Fog / Low Clouds picture sets how thick (style_height): without it the same (the owner's photo,
    # 26.10.2026 23:14 Riga, thin altocumulus with a corona; our 19:00 UTC frame made without it was 86 % cloud round
    # Riga, 43 % made again with it).
    lacking = []
    if "visible" in jobs and visible is None:
        visible = picture(VISIBLE, ten - timedelta(minutes=10), box, (width, height))
        if visible is None:
            lacking.append("the visible")
    if daylight.min() < 1 and got.get("fog") is None:
        got["fog"] = picture(FOG, ten - timedelta(minutes=10), box, (width // 4, height // 4))
        if got["fog"] is None:
            lacking.append("the fog picture")
    if lacking and not incomplete:
        return None, "not in yet: " + " or ".join(lacking)
    how = "without " + " and ".join(lacking) if lacking else "complete"
    mask = with_low_cloud(mask, got.get("fog"))
    # The morning: as the sun comes up the fog picture loses the low deck (its 3.9 um channel sees sunlight; Riga
    # 25.10.2026, OVC004 all morning: seen until 05:20 UTC, lost from 05:50) and the mask is still blind to it. Where
    # the sun is rising and under 12 deg, the fog pictures of the last two hours count too, each where the sun was
    # still below the horizon when it was taken (four days' morning METARs: 40 of the 47 overcast reports still missed
    # caught, 5 clear reports of 72 called cloud, decks that broke up after sunrise; looking back one hour: 25 and 1).
    # Three hours and under 15 deg since 28.10.2026 (the owner's screenshots: east Latvia clear at 09:15-09:30 Riga
    # under one deck, Riga OVC002 04:20-08:20 UTC, Šiauliai fog; the mask saw it only from 07:45 UTC, the sun ~15 deg
    # there; "A+B, check on the morning METARs first"): seven mornings (22-28.10, 20 airports, 03:15-13:45 UTC,
    # 1,494 overcast reports, 212 missed without the memory, 748 clear): 2 h and 12 deg caught 84, called 11 clear
    # cloud; 3 h 93 and 17, and 15 deg 18; 4 h 94-96 and 22-24.
    def grid(a):
        return np.asarray(Image.fromarray(a.astype(np.uint8) * 255).resize((mask.shape[1], mask.shape[0]), Image.NEAREST)) > 127

    sun_now = sun_height(lats[:, None], lons[None, :], when)
    rising = (sun_now >= -6) & (sun_now < 15) & (sun_now > sun_height(lats[:, None], lons[None, :], when - timedelta(minutes=30)))
    if rising.any():
        for minutes in (30, 60, 90, 120, 150, 180):
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
        sun = np.asarray(Image.fromarray(sun_now.astype(np.float32)).resize((width, height), Image.BILINEAR), dtype=np.float32)
        fog = got.get("fog")
        if fog is not None:
            fog = np.asarray(Image.fromarray(fog.astype(np.uint8)).resize((width, height), Image.BILINEAR), dtype=np.float32)
        shade, alpha = style_height(infrared, mask, visible, heights, daylight, fog, sun)
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
    return world_tiles.encode(tiles, {z: 256 for z in TILE_ZOOMS}), how


def sat_published():
    """The quarter hours already in the tiles' index: {stamp: its entry ({stamp, time, file}, "visible": False when
    made without a picture that says how thick: the visible or the fog picture)}."""
    try:
        request = urllib.request.Request(TILES_INDEX + f"?t={int(time.time())}", headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=30) as reply:
            return {f["stamp"]: f for f in json.load(reply).get("frames", [])}
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return {}


# A frame waits for the pictures that say how thick until this long after its time, then goes without them (no gap in
# the clouds; the owner, 26.10.2026: "build A"); the frames are made about 30 minutes after their time.
WAIT_VISIBLE = 45 * 60


def make_tiles(wanted, token, remake=None):
    """Europe's missing tile packs, newest first; then one made without the visible or the fog picture, again now it
    may be there (the net). `remake`: these quarter hours made again instead (--remake), each kept as it was when a
    picture doesn't come."""
    have = sat_published()
    if remake is None:
        missing = [t for t in wanted if t.strftime("%Y%m%d%H%M") not in have][:TILES_PER_RUN]
        again = [t for t in wanted if have.get(t.strftime("%Y%m%d%H%M"), {}).get("visible") is False][:1]
        print(f"Satellite tiles: {len(have)} published, {len(missing)} to make, {len(again)} to make again complete")
    else:
        missing, again = [], [t for t in remake if t.strftime("%Y%m%d%H%M") in have]
        print(f"Satellite tiles: {len(again)} to make again")
    now = time.time()
    for when in missing + again:
        started = time.time()
        stamp = when.strftime("%Y%m%d%H%M")
        before = have.get(stamp)
        pack, how = sat_pack(when, incomplete=before is None and now - when.timestamp() > WAIT_VISIBLE)
        if pack is None:
            print(f"  {when:%H:%M}: {how}" + (", kept as it was" if before else ", tried again next run" if how != "no picture" else ""))
            continue
        name = stamp
        if before:
            # Its own name (the packs are cached for a year): <stamp>-r<n>.
            done = before["file"].removesuffix(".pack").partition("-r")[2]
            name = f"{stamp}-r{int(done or 0) + 1}"
        try:
            send(TILES_UPLOAD + name + ".pack" + ("?visible=0" if how != "complete" else ""), pack,
                 "application/octet-stream", token)
            print(f"  {when:%Y-%m-%d %H:%M}: {len(pack) // 1024} KB, {time.time() - started:.0f} s, {how}"
                  + (" (made again)" if before else ""))
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


# The home runner's shadow (--local DIR): the pictures and packs written under DIR, nothing uploaded (the Tower's jobs
# have the upload token; 26.10.2026).
LOCAL = None


def send(url, data, content_type, token):
    """One picture or pack: uploaded, or under LOCAL (its path after the upload address's /upload/)."""
    if LOCAL:
        kind, _, rest = url.partition("/upload/")
        path = LOCAL / kind.rsplit("/", 1)[-1] / rest.partition("?")[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return
    request = urllib.request.Request(url, data=data, method="PUT",
                                     headers={"Authorization": "Bearer " + token, "Content-Type": content_type, "User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=120) as reply:
        reply.read()


def listed_until(layer):
    """The newest time EUMETSAT's map server lists for a layer (its GetCapabilities' time dimension, "start/end/step"
    ranges or single times), or None."""
    workspace, name = layer.split(":")
    url = f"https://view.eumetsat.int/geoserver/{workspace}/{name}/ows?service=WMS&version=1.3.0&request=GetCapabilities"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=30) as reply:
            text = reply.read().decode("utf-8", "replace")
        extent = text.split('<Dimension name="time"', 1)[1].split(">", 1)[1].split("<", 1)[0]
        last = extent.strip().split(",")[-1].split("/")
        return datetime.strptime((last[1] if len(last) > 1 else last[0])[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (urllib.error.URLError, OSError, http.client.HTTPException, IndexError, ValueError):
        return None


def newest_ready(now):
    """The newest quarter hour whose pictures are all listed: MSG's mask and cloud top height at it, MTG's infrared, fog
    and visible at its 10-minute step (the owner, 26.10.2026: "make each cloud frame as soon as all its pictures are
    listed"; frames came 31-35 min after their time, EUMETSAT lists them after about 22). Not merely a shorter wait:
    asked for a time it hasn't got, the map server answers with the newest older picture (21:55's infrared came back as
    21:40's), so a frame could mix a new infrared with an old mask. Unlisted (the server down): 30 minutes old, as before."""
    until = {layer: listed_until(layer) for layer in (MASK, HEIGHT, INFRARED, FOG, VISIBLE)}
    if all(until.values()):
        quarter = min(until[MASK], until[HEIGHT]).replace(second=0, microsecond=0)
        quarter -= timedelta(minutes=quarter.minute % 15)
        _, _, _, lats, lons = sat_box()

        def ready(q):
            ten = q - timedelta(minutes=q.minute % 10)
            # The visible only where sat_pack asks it: the sun over 3 degrees somewhere in the box (by night it's listed
            # 10 minutes behind the others and not used).
            day = bool((sun_height(lats[:, None], lons[None, :], q) > 3).any())
            return ten <= min(until[INFRARED], until[FOG]) and (not day or ten <= until[VISIBLE])

        while not ready(quarter):
            quarter -= timedelta(minutes=15)
        print(f"Listed: the mask to {until[MASK]:%H:%M}, the infrared to {until[INFRARED]:%H:%M}: frames to {quarter:%H:%M} "
              f"({(now - quarter).total_seconds() / 60:.0f} min ago)")
        return quarter
    print("Not listed: frames at least 30 minutes old")
    newest = (now - timedelta(minutes=30)).replace(second=0, microsecond=0)
    return newest - timedelta(minutes=newest.minute % 15)


def main():
    global LOCAL
    if "--local" in sys.argv:
        LOCAL = Path(sys.argv[sys.argv.index("--local") + 1])
    token = os.environ.get("INGEST_TOKEN", "")
    if not token and not LOCAL:
        print("No INGEST_TOKEN: no clouds sent")
        return
    now = datetime.now(timezone.utc)
    newest = newest_ready(now)
    wanted = [newest - timedelta(minutes=15 * k) for k in range(96)]
    if "--remake" in sys.argv:
        # A one-off: the tile packs from FROM to TO (YYYYMMDDHHMM, UTC) made again with all their pictures, the
        # Baltic set left as it is (26.10.2026: the frames published without the visible before the net).
        i = sys.argv.index("--remake")
        start, end = (datetime.strptime(s, "%Y%m%d%H%M").replace(tzinfo=timezone.utc) for s in sys.argv[i + 1:i + 3])
        make_tiles(wanted, token, remake=[t for t in wanted if start <= t <= end])
        return
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
        for minutes in (10, 20):
            if infrared is None:
                infrared = picture(INFRARED, when - timedelta(minutes=when.minute % 10 + minutes), one["box"], one["size"])
        mask = picture(MASK, when, one["box"], one["size"])
        if infrared is None or mask is None:
            print(f"  {when:%H:%M}: no picture ({'infrared' if infrared is None else 'mask'})")
            continue
        mask = with_low_cloud(mask, picture(FOG, when - timedelta(minutes=when.minute % 10), one["box"], one["size"]))
        data, ground = render(infrared, mask, one["local"], one["quality"], one["alpha"])
        stamp = when.strftime("%Y%m%d%H%M")
        try:
            send(UPLOAD + one["folder"] + stamp, data, "image/webp", token)
            made += 1
            print(f"  {when:%Y-%m-%d %H:%M}: {len(data) // 1024} KB, clear ground grey {ground:.0f}")
        except urllib.error.URLError as error:
            print(f"  {when:%H:%M}: upload failed: {error}")
    print(f"Clouds ({name}): {made} made")


if __name__ == "__main__":
    sys.exit(main())
