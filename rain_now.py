"""The rain's first hours from the radar into the forecast (the owner, 14.10.2026: the nowcast and
the forecast looked nothing alike, the model's front 100 km off and turned, its light rain far
wider than the radar's), every hour:

1. **Measure.** At the newest whole radar hour, each forecast set's rain for that hour against the
   radar (where a radar sees): how much more ground is wet (at 0.1, 0.5 and 1 mm/h), the fractions
   skill score (40 km, 0.5 mm/h), and how far its rain is off (phase correlation in 250 km cells).
   Kept in tiles/rain-now/skill.json (the latest and two weeks of hours) for the admin panel.
2. **Correct and blend.** The app's forecast (its Auto order: MET Nordic 1 km, the regional
   composite, ICON-EU, ECMWF) shifted by the measured offset, the shift fading out over 8 hours;
   its too-wide light rain trimmed to the radar's wet area, fading the same; and the radar's own
   rain carried along its motion (the nowcast) blended into it over the first 3.5 hours. Hourly
   frames from the next whole hour, 7 of them, as tiles/rain-now (zooms 3-6, the rain encoding:
   sqrt(mm/h) x 50, the cloud cover, the weight as alpha), for the app's rain map after the nowcast.

Reads our own published tiles (files.meteogust.com), writes through station-history.

    python rain_now.py [--local DIR]
"""
import io
import json
import math
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
from PIL import Image
from scipy import ndimage

import tiler
import world_tiles

FILES = "https://files.meteogust.com/tiles/"
Z = 6
SETS = ["metnordic-1km-wx", "metnordic-wx", "icon-eu-wx", "ecmwf"]
HOURS = 7
# The measured shift fades out over this many hours; the nowcast's share from 1 at half an hour to
# 0 at 3.5 hours.
SHIFT_HOURS, NOW_FROM, NOW_TO = 8.0, 0.5, 3.5
# Offsets: cells of 96 zoom-5 pixels (about 250 km here), every 48, the search within 48 (about 130 km).
CELL, STEP, REACH = 96, 48, 48
WET = 0.1


# MARK: Reading packs

def get(url, tries=4):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url + ("&" if "?" in url else "?") + "t=%d" % time.time() if url.endswith(".json") else url,
                                             headers={"User-Agent": "MeteoGust rain-now"})
            with urllib.request.urlopen(request, timeout=120) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code == 404 or attempt == tries - 1:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == tries - 1:
                raise
        time.sleep(5 * (attempt + 1))


def read_pack(data):
    """A pack's sizes and its tiles' PNG bytes by (z, x, y)."""
    length = int.from_bytes(data[4:8], "little")
    header = json.loads(data[8:8 + length])
    start = 8 + length
    tiles = {}
    for key, (offset, size) in header["tiles"].items():
        z, x, y = (int(v) for v in key.split("/"))
        tiles[(z, x, y)] = data[start + offset:start + offset + size]
    return {int(z): s for z, s in header.get("sizes", {}).items()}, tiles


def decode(png):
    return np.asarray(Image.open(io.BytesIO(png)))


class Domain:
    """The zoom-6 tiles the radar reaches (a rectangle of them) as one picture."""

    def __init__(self, keys):
        xs, ys = [k[1] for k in keys if k[0] == Z], [k[2] for k in keys if k[0] == Z]
        self.x0, self.x1, self.y0, self.y1 = min(xs), max(xs), min(ys), max(ys)
        self.width, self.height = (self.x1 - self.x0 + 1) * 256, (self.y1 - self.y0 + 1) * 256
        self._grids = {}

    def place(self, tiles, z, size, channels):
        """A pack's tiles at zoom z (side `size`) sampled onto the domain: (H, W, channels) floats,
        0 where it has none (alpha 0)."""
        k = Z - z
        px0, px1, py0, py1 = self.x0 >> k, self.x1 >> k, self.y0 >> k, self.y1 >> k
        mosaic = np.zeros(((py1 - py0 + 1) * size, (px1 - px0 + 1) * size, channels), np.float32)
        for (tz, tx, ty), png in tiles.items():
            if tz != z or not (px0 <= tx <= px1 and py0 <= ty <= py1):
                continue
            image = decode(png).astype(np.float32)
            if image.ndim == 2:
                image = image[..., None]
            # A tile without its weight (ECMWF's RGB): it counts fully.
            if image.shape[2] < channels:
                image = np.concatenate([image, np.full(image.shape[:2] + (channels - image.shape[2],), 255, np.float32)], -1)
            if image.shape[0] != size or image.shape[1] != size:
                continue
            mosaic[(ty - py0) * size:(ty - py0 + 1) * size, (tx - px0) * size:(tx - px0 + 1) * size, :] = image[..., :channels]
        if k == 0 and size == 256:
            return mosaic
        key = (z, size)
        if key not in self._grids:
            scale = size / (256 * 2 ** k)
            rows = (self.y0 * 256 + np.arange(self.height) + 0.5) * scale - py0 * size - 0.5
            cols = (self.x0 * 256 + np.arange(self.width) + 0.5) * scale - px0 * size - 0.5
            self._grids[key] = np.meshgrid(rows.astype(np.float32), cols.astype(np.float32), indexing="ij")
        r, c = self._grids[key]
        return np.stack([ndimage.map_coordinates(mosaic[..., i], [r, c], order=1, mode="nearest") for i in range(channels)], -1)


def radar_field(domain, data):
    """The radar's mm/h on the domain, NaN where no radar sees."""
    sizes, tiles = read_pack(data)
    la = domain.place(tiles, Z, 256, 2)
    mm = (la[..., 0] / 50) ** 2
    return np.where(la[..., 1] > 127, mm, np.nan).astype(np.float32)


class Forecast:
    """One set's newest run: its rain frames by time."""

    def __init__(self, name):
        self.name = name
        index = json.loads(get(FILES + name + "/index.json"))
        self.run = index.get("run")
        self.frames = {f["time"]: f["rain"] for f in index.get("frames", []) if f.get("rain")}
        self.zooms = index.get("zooms", [0, 3])
        self.cache = {}
        # The measured hour's fields are kept (read again for the composite).
        self.keep = None

    def field(self, domain, t):
        """mm/h, the cloud cover (0-1) and the weight (0-1) on the domain at time t; None without it."""
        if t not in self.frames:
            return None
        if t in self.cache:
            return self.cache[t]
        sizes, tiles = read_pack(get(FILES + self.name + "/" + self.frames[t]))
        z = min(self.zooms[1], Z)
        rgba = domain.place(tiles, z, sizes.get(z, 256), 4)
        got = ((rgba[..., 0] / 50) ** 2, rgba[..., 1] / 255, rgba[..., 3] / 255)
        if t == self.keep:
            self.cache[t] = got
        return got


def composite(sets, domain, t):
    """The app's Auto order at time t, each over the ones after it by its weight: mm/h, cloud, weight."""
    shape = (domain.height, domain.width)
    rain, cloud, remaining = np.zeros(shape, np.float32), np.zeros(shape, np.float32), np.ones(shape, np.float32)
    for forecast in sets:
        got = forecast.field(domain, t)
        if got is None:
            continue
        mm, cover, weight = got
        share = weight * remaining
        rain += share * mm
        cloud += share * cover
        remaining *= 1 - weight
    total = 1 - remaining
    safe = np.maximum(total, 1e-6)
    return rain / safe, cloud / safe, total


# MARK: Measuring

def half(a):
    """Zoom 6 to 5 (means of 2 x 2; NaN where all four are)."""
    h, w = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
    with np.errstate(invalid="ignore"):
        return np.nanmean(a[:h, :w].reshape(h // 2, 2, w // 2, 2), axis=(1, 3))


def fss(a, b, scale):
    fa, fb = ndimage.uniform_filter(a.astype(np.float32), scale), ndimage.uniform_filter(b.astype(np.float32), scale)
    mse = np.mean((fa - fb) ** 2)
    ref = np.mean(fa ** 2) + np.mean(fb ** 2)
    return float(1 - mse / ref) if ref > 0 else None


def offsets(a, b, known, reach=REACH, cell=CELL, step=STEP, least=0.03):
    """Where b's pattern lies against a's, cell by cell: [(row, col, dy, dx, gain)], b(x - d) ~ a(x)
    (pixels of the grid given; gain: how much better the shifted match is than none)."""
    out = []
    window = np.outer(np.hanning(cell), np.hanning(cell)).astype(np.float32)
    la, lb = np.where(known, np.log1p(np.nan_to_num(a)), 0), np.where(known, np.log1p(np.nan_to_num(b)), 0)
    for r in range(0, a.shape[0] - cell + 1, step):
        for c in range(0, a.shape[1] - cell + 1, step):
            k = known[r:r + cell, c:c + cell]
            if k.mean() < 0.5:
                continue
            pa, pb = la[r:r + cell, c:c + cell], lb[r:r + cell, c:c + cell]
            if (pa > math.log1p(WET)).mean() < least or (pb > math.log1p(WET)).mean() < least:
                continue
            pa, pb = (pa - pa.mean()) * window, (pb - pb.mean()) * window
            size = 2 * cell
            corr = np.real(np.fft.ifft2(np.fft.fft2(pa, (size, size)) * np.conj(np.fft.fft2(pb, (size, size)))))
            corr = np.fft.fftshift(corr)
            mid = size // 2
            box = corr[mid - reach:mid + reach + 1, mid - reach:mid + reach + 1]
            norm = math.sqrt(float((pa ** 2).sum() * (pb ** 2).sum())) or 1
            i, j = np.unravel_index(np.argmax(box), box.shape)
            best, none = box[i, j] / norm, corr[mid, mid] / norm
            dy, dx = float(i - reach), float(j - reach)
            # Sub-pixel: a parabola through the peak's neighbours.
            if 0 < i < box.shape[0] - 1:
                den = box[i - 1, j] - 2 * box[i, j] + box[i + 1, j]
                dy += float(0.5 * (box[i - 1, j] - box[i + 1, j]) / den) if den else 0
            if 0 < j < box.shape[1] - 1:
                den = box[i, j - 1] - 2 * box[i, j] + box[i, j + 1]
                dx += float(0.5 * (box[i, j - 1] - box[i, j + 1]) / den) if den else 0
            if best < 0.25:
                continue
            out.append((r + cell / 2, c + cell / 2, dy, dx, float(best - none), float(best)))
    return out


def field_of(cells, shape, sigma, least_gain=0.02, default=(0.0, 0.0)):
    """Cell vectors spread over the grid (Gaussian, `sigma` pixels), towards `default` where few."""
    vy, vx, w = np.zeros(shape, np.float32), np.zeros(shape, np.float32), np.zeros(shape, np.float32)
    for r, c, dy, dx, gain, _ in cells:
        if gain < least_gain:
            dy = dx = 0.0
        ri, ci = int(min(max(r, 0), shape[0] - 1)), int(min(max(c, 0), shape[1] - 1))
        weight = max(gain, 0.02) * 20
        vy[ri, ci] += dy * weight
        vx[ri, ci] += dx * weight
        w[ri, ci] += weight
    vy, vx, w = (ndimage.gaussian_filter(v, sigma) for v in (vy, vx, w))
    # A little "no shift" everywhere: far from any measured cell the shift goes to nothing.
    floor = w.max() * 0.15 if w.max() > 0 else 1
    return (vy + floor * default[0]) / (w + floor), (vx + floor * default[1]) / (w + floor)


def measure(radar, sets, domain, t, checked=()):
    """Each set's (and the composite's) rain at t against the radar's: the skill and the offset;
    `checked`: sets measured but not part of the composite (our own blend from an hour before)."""
    r5 = half(radar)
    out = {}
    for forecast in list(sets) + ["composite"] + list(checked):
        if forecast == "composite":
            mm, _, weight = composite(sets, domain, t)
            name, run = "composite", None
        else:
            got = forecast.field(domain, t)
            if got is None:
                continue
            mm, _, weight = got
            name, run = forecast.name, forecast.run
        m5, w5 = half(mm), half(weight)
        known = np.isfinite(r5) & (w5 > 0.5)
        if known.mean() < 0.02:
            continue
        a, b = np.nan_to_num(r5), np.nan_to_num(m5)
        row = {"run": run, "cells": int(known.sum())}
        if run:
            lead = (t - datetime.strptime(run, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp()) / 3600
            row["leadHours"] = round(lead, 1)
        for threshold in (0.1, 0.5, 1.0):
            ra, mb = (a >= threshold) & known, (b >= threshold) & known
            hits = int((ra & mb).sum())
            row[f"wet{threshold}"] = {"radar": round(float(ra.sum()) / known.sum(), 4), "model": round(float(mb.sum()) / known.sum(), 4),
                                      "csi": round(hits / max(int((ra | mb).sum()), 1), 3)}
        row["fss40km"] = fss((a >= 0.5) & known, (b >= 0.5) & known, 15)
        cells = offsets(a, b, known)
        good = [c for c in cells if c[4] >= 0.02]
        km = 2 * 40075 / (256 * 2 ** Z) * math.cos(math.radians(57))  # a zoom-5 pixel near 57 N
        row["offset"] = {"cells": len(cells), "moved": len(good),
                         "medianKm": round(float(np.median([math.hypot(c[2], c[3]) for c in good])) * km, 1) if good else 0,
                         "list": [[round(c[0]), round(c[1]), round(c[2], 1), round(c[3], 1), round(c[4], 3)] for c in cells]}
        out[name] = row
    return out


# MARK: The blend

def motion(newest, before, minutes):
    """The radar's motion (zoom-6 pixels an hour, rows and columns) from two pictures."""
    a, b = half(newest), half(before)
    k5 = np.isfinite(a) & np.isfinite(b)
    cells = offsets(a, b, k5, reach=12, cell=64, step=32, least=0.02)
    good = [c for c in cells if c[5] >= 0.4]
    if not good:
        return np.zeros(newest.shape, np.float32), np.zeros(newest.shape, np.float32), 0
    # The shift of b (older) onto a (newer): d pixels at zoom 5 in `minutes`; where nothing was
    # measured, the median of what was.
    median = (float(np.median([c[2] for c in good])), float(np.median([c[3] for c in good])))
    vy5, vx5 = field_of([(r, c, dy, dx, 1.0, s) for r, c, dy, dx, g, s in good], a.shape, 40, default=median)
    scale = 2 * 60 / minutes
    return to_zoom6(vy5 * scale, newest.shape), to_zoom6(vx5 * scale, newest.shape), len(good)


def to_zoom6(v5, shape):
    """A zoom-5 field onto the zoom-6 grid (values unchanged)."""
    v = ndimage.zoom(v5, 2, order=1)[:shape[0], :shape[1]]
    return np.pad(v, ((0, shape[0] - v.shape[0]), (0, shape[1] - v.shape[1])), mode="edge").astype(np.float32)


def advect(field, vy, vx, hours):
    rows, cols = np.meshgrid(np.arange(field.shape[0], dtype=np.float32), np.arange(field.shape[1], dtype=np.float32), indexing="ij")
    return ndimage.map_coordinates(field, [rows - vy * hours, cols - vx * hours], order=1, mode="constant", cval=np.nan)


def trim_level(radar, mm, known):
    """The rain rate above which the model is as wet as the radar is above 0.1 mm/h (0.1 when the
    model isn't wetter)."""
    r, m = np.nan_to_num(radar)[known], np.nan_to_num(mm)[known]
    share = float((r >= WET).mean()) if r.size else 0
    if share <= 0 or float((m >= WET).mean()) <= share:
        return WET
    return max(WET, float(np.quantile(m, 1 - share)))


def frames(radar, newest_time, vy, vx, sets, domain, t0, shift, trim):
    """The blended hours (time, mm/h, cloud, weight)."""
    first = (newest_time // 3600 + 1) * 3600
    sy, sx = shift
    rows, cols = np.meshgrid(np.arange(domain.height, dtype=np.float32), np.arange(domain.width, dtype=np.float32), indexing="ij")
    out = []
    for k in range(HOURS):
        t = first + k * 3600
        lead = (t - newest_time) / 3600
        mm, cloud, weight = composite(sets, domain, t)
        if not np.any(weight > 0):
            continue
        fade = max(0.0, 1 - (t - t0) / 3600 / SHIFT_HOURS)
        if fade > 0:
            at = [rows - sy * fade, cols - sx * fade]
            mm = ndimage.map_coordinates(mm, at, order=1, mode="nearest")
            cloud = ndimage.map_coordinates(cloud, at, order=1, mode="nearest")
            weight = ndimage.map_coordinates(weight, at, order=1, mode="nearest")
            level = WET + (trim - WET) * fade
            mm = np.where(mm >= level, mm, 0)
        now = advect(radar, vy, vx, lead)
        share = float(np.clip((NOW_TO - lead) / (NOW_TO - NOW_FROM), 0, 1))
        w = np.where(np.isfinite(now), share, 0).astype(np.float32)
        blended = w * np.nan_to_num(now) + (1 - w) * mm
        out.append((t, blended, cloud, np.maximum(weight, w)))
    return out



def pack(domain, mm, cloud, weight):
    """Tiles at zooms 3-6 (each zoom down: the means of 2 x 2)."""
    tiles = {}
    level_mm, level_cloud, level_w = mm, cloud, weight
    for z in range(Z, 2, -1):
        k = Z - z
        x0, y0 = domain.x0 >> k, domain.y0 >> k
        h, w = level_mm.shape
        for ty in range(h // 256 + (1 if h % 256 else 0)):
            for tx in range(w // 256 + (1 if w % 256 else 0)):
                sl = (slice(ty * 256, (ty + 1) * 256), slice(tx * 256, (tx + 1) * 256))
                a = level_w[sl]
                if a.shape != (256, 256) or not np.any(a > 0.004):
                    continue
                rgb = tiler.rain_rgb(level_mm[sl], level_cloud[sl])
                alpha = np.clip(np.rint(a * 255), 0, 255)
                tiles[(z, x0 + tx, y0 + ty)] = world_tiles.png(np.concatenate([rgb, alpha[..., None]], -1).astype(np.uint8), "RGBA")
        if z > 3:
            # The next zoom out: whole parent tiles (a tile's padding when the corner is odd), then
            # the means of 2 x 2.
            py, px = ((domain.y0 >> k) % 2) * 256, ((domain.x0 >> k) % 2) * 256

            def down(v):
                v = np.pad(v, ((py, 0), (px, 0)))
                hh, ww = -(-v.shape[0] // 512) * 512, -(-v.shape[1] // 512) * 512
                v = np.pad(v, ((0, hh - v.shape[0]), (0, ww - v.shape[1])))
                return v.reshape(hh // 2, 2, ww // 2, 2).mean(axis=(1, 3))
            level_mm, level_cloud, level_w = down(level_mm), down(level_cloud), down(level_w)
    return world_tiles.encode(tiles, {z: 256 for z in range(3, Z + 1)})


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    started = time.time()
    radar_index = json.loads(get(FILES + "radar/index.json"))
    radar_frames = sorted(radar_index["frames"], key=lambda f: f["time"])
    newest = radar_frames[-1]
    by_time = {f["time"]: f["file"] for f in radar_frames}
    hours = [t for t in by_time if t % 3600 == 0]
    if not hours:
        raise SystemExit("no whole radar hour")
    t0 = max(hours)
    before_time = max((t for t in by_time if t <= newest["time"] - 1800), default=None)
    if before_time is None:
        raise SystemExit("no radar half an hour before the newest")
    packs = {}
    with ThreadPoolExecutor(3) as pool:
        for t, data in zip([newest["time"], t0, before_time],
                           pool.map(lambda t: get(FILES + "radar/" + by_time[t]), [newest["time"], t0, before_time])):
            packs[t] = data
    _, keys = read_pack(packs[newest["time"]])
    domain = Domain(list(keys))
    print(f"radar {datetime.fromtimestamp(newest['time'], timezone.utc):%H:%M}, hour {datetime.fromtimestamp(t0, timezone.utc):%H:%M}; "
          f"domain {domain.width}x{domain.height}", flush=True)
    radar_new = radar_field(domain, packs[newest["time"]])
    radar_hour = radar_field(domain, packs[t0])
    radar_before = radar_field(domain, packs[before_time])
    sets = []
    for name in SETS:
        try:
            sets.append(Forecast(name))
            sets[-1].keep = t0
        except Exception as error:
            print(f"{name}: not read ({error})", flush=True)

    # 1. Measure (our own blend from the run before too: is it nearer the radar than the forecast?).
    checked = []
    try:
        checked.append(Forecast("rain-now"))
    except Exception as error:
        print(f"rain-now: no run before ({error})", flush=True)
    skill = measure(radar_hour, sets, domain, t0, checked)
    for name, row in skill.items():
        print(f"  {name}: lead {row.get('leadHours')} h, wet 0.1 radar {row['wet0.1']['radar']:.3f} model {row['wet0.1']['model']:.3f}, "
              f"csi {row['wet0.5']['csi']}, fss {row['fss40km']}, offset {row['offset']['medianKm']} km in {row['offset']['moved']}"
              f"/{row['offset']['cells']} cells", flush=True)

    # 2. The composite's offset over the domain (zoom 6 pixels), its trimming level, the radar's motion.
    comp = skill.get("composite")
    shape5 = half(radar_hour).shape
    if comp and comp["offset"]["list"]:
        cells = [(r, c, dy, dx, g, 1.0) for r, c, dy, dx, g in comp["offset"]["list"]]
        oy5, ox5 = field_of(cells, shape5, 60)
        shift = (to_zoom6(oy5 * 2, (domain.height, domain.width)), to_zoom6(ox5 * 2, (domain.height, domain.width)))
    else:
        shift = (np.zeros((domain.height, domain.width), np.float32), np.zeros((domain.height, domain.width), np.float32))
    mm0, _, w0 = composite(sets, domain, t0)
    known0 = np.isfinite(radar_hour) & (w0 > 0.5)
    trim = trim_level(radar_hour, mm0, known0)
    vy, vx, measured = motion(radar_new, radar_before, (newest["time"] - before_time) / 60)
    print(f"trim below {trim:.2f} mm/h; motion from {measured} cells, median "
          f"{float(np.median(np.hypot(vy, vx))) * 40075 / (256 * 2 ** Z) * math.cos(math.radians(57)):.0f} km/h; "
          f"{time.time() - started:.0f} s", flush=True)

    run_time = datetime.fromtimestamp(newest["time"], timezone.utc)
    out = tiler.Run("rain-now", run_time, range(3, Z + 1), "Radar into the forecast", "EUMETNET OPERA (CC BY 4.0); the forecast sets' own",
                    True, local)
    for t, mm, cloud, weight in frames(radar_new, newest["time"], vy, vx, sets, domain, t0, shift, trim):
        out.report.count()
        out.put(t, pack(domain, mm, cloud, weight), kind="rain")
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%H:%M}: {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    summary = {name: {k: row[k] for k in row if k != "offset"} | {"offsetKm": row["offset"]["medianKm"], "moved": row["offset"]["moved"],
                                                                   "cellsCompared": row["offset"]["cells"]}
               for name, row in skill.items()}
    out.finish(radar=newest["time"], hour=t0, trimMm=round(trim, 3), skill=summary)
    record = {"hour": t0, "radar": newest["time"], "skill": summary}
    try:
        kept = json.loads(get(FILES + "rain-now/skill.json"))
    except Exception:
        kept = {"hours": []}
    hours_kept = [h for h in kept.get("hours", []) if h.get("hour") != t0 and h.get("hour", 0) > t0 - 14 * 86400] + [record]
    body = json.dumps({"latest": record | {"offsets": {n: r["offset"]["list"] for n, r in skill.items()}}, "hours": hours_kept},
                      separators=(",", ":")).encode()
    if local:
        open(f"{local}/skill.json", "wb").write(body)
    else:
        world_tiles.upload("rain-now/skill.json", body)
    print(f"done in {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
