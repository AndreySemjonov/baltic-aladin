"""A blended wind map of the Latvian coast: every model map built in this run, mixed
per pixel and hour with fixed weights, like a multi-model point forecast but for the
whole map.

Reads the frames the other scripts wrote under the site folder (any model that is
missing is left out), puts them on the MET Nordic map's pixels (all maps are Web
Mercator, so that is a linear step), interpolates 3-hourly models in time, and mixes:
wind and gust as weighted means, direction as a weighted vector mean (weight x speed,
calm counted as 1 m/s). Where or when a model has no data, the others share its weight.
Hourly to 72 hours, then every 3 hours to 240 hours; each frame lists the models in it.
Format in maplib.py.

    python blend_map.py site            reads site/<model>/, writes site/blend/
"""
import json
import math
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

import maplib
import metnordic_map

# Model folder -> weight: the shares of the app's Baltic forecast blend for the models
# that have a map here (renormalized wherever some are missing). ALADIN is left out since
# 7.10.2026: it covers only Kurzeme to Riga, and its edge showed across the map.
WEIGHTS = {"harm-dk": 0.226, "map": 0.181, "harmonie": 0.181,
           "icon-eu": 0.068, "ecmwf": 0.034, "gfs": 0.023}
# How far each model's land reaches over the sea, km (about 1.5 of its grid cells).
COAST_KM = {"harm-dk": 3.0, "map": 1.5, "harmonie": 3.75, "aladin": 3.5, "icon-eu": 10.0, "ecmwf": 25.0, "gfs": 25.0}
NAMES = {"harm-dk": "HARM-DK", "map": "MET Nordic", "harmonie": "HARM-FI", "aladin": "ALADIN",
         "icon-eu": "ICON-EU", "ecmwf": "ECMWF", "gfs": "GFS"}
SOUTH, NORTH, WEST, EAST = metnordic_map.SOUTH, metnordic_map.NORTH, metnordic_map.WEST, metnordic_map.EAST
MAX_GAP = 6 * 3600  # frames further apart than this aren't interpolated
# A model that covers only part of the box (ALADIN, the 1-2.5 km models at their edges) fades out
# over its last 30 km, so the blend has no straight line where it stops (7.10.2026).
EDGE_KM = 30.0


class Model:
    """One model's published frames, read on demand and placed on the blend's pixels."""

    def __init__(self, folder, lat, lon, share=None, coast_km=0):
        self.folder = folder
        self.share, self.coast_km = share, coast_km
        manifest = json.loads((folder / "map.json").read_text(encoding="utf-8"))
        self.frames = {frame["time"]: frame["file"] for frame in manifest["frames"]}
        self.times = sorted(self.frames)
        b = manifest["bounds"]
        width, height = manifest["width"], manifest["height"]
        top, bottom = maplib.mercator_y(b["north"]), maplib.mercator_y(b["south"])
        fx = (lon - b["west"]) / (b["east"] - b["west"]) * width - 0.5
        fy = (top - maplib.mercator_y(lat)) / (top - bottom) * height - 0.5
        self.sample = maplib.Bilinear(fx, fy, width, height)
        self.cache = {}
        self.tapers = {}

    def taper(self, inside):
        """0 at the edge of the model's area to 1 at EDGE_KM inside it (1 everywhere when it
        covers the whole box); the same area gives the same taper, worked out once."""
        if inside.all():
            return np.ones(inside.shape, np.float32)
        key = hash(inside.tobytes())
        if key not in self.tapers:
            distance = ndimage.distance_transform_edt(inside) * metnordic_map.KM_PER_PIXEL
            self.tapers[key] = np.clip(distance / EDGE_KM, 0, 1).astype(np.float32)
        return self.tapers[key]

    def frame(self, t):
        """u, v, speed, gust (m/s) and validity at the blend's pixels for frame time t."""
        if t not in self.cache:
            pixels = np.asarray(Image.open(self.folder / self.frames[t]).convert("RGBA")).astype(np.float32)
            speed, degrees, gust = pixels[..., 0] / 5, np.radians(pixels[..., 1] * 360 / 256), pixels[..., 2] / 5
            inside = (pixels[..., 3] >= 128).astype(np.float32)
            # Speed-weighted components, so 350° and 10° don't average to 180°.
            u, v = -speed * np.sin(degrees), -speed * np.cos(degrees)
            values = [self.sample(a).astype(np.float32) for a in (u, v, speed, gust)]
            # Only inside the model's own map: outside it the sampler repeats the edge (ALADIN's
            # edge values were smeared over the rest of the box until 7.10.2026).
            inside = (self.sample(inside) > 0.99) & self.sample.valid
            maplib.coast_fill(values, self.share, self.coast_km, metnordic_map.KM_PER_PIXEL, valid=inside)
            self.cache[t] = values + [inside, self.taper(inside)]
        return self.cache[t]

    def at(self, t):
        """The model at any time inside its frames: exact, or linear between two frames."""
        if t in self.frames:
            return self.frame(t)
        later = next((x for x in self.times if x > t), None)
        earlier = next((x for x in reversed(self.times) if x < t), None)
        if later is None or earlier is None or later - earlier > MAX_GAP:
            return None
        share = (t - earlier) / (later - earlier)
        a, b = self.frame(earlier), self.frame(later)
        return [x + (y - x) * share for x, y in zip(a[:4], b[:4])] + [a[4] & b[4], np.minimum(a[5], b[5])]


def main():
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    out = site / "blend"
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, metnordic_map.KM_PER_PIXEL)
    share = maplib.land_share(lat, lon)
    models = {name: Model(site / name, lat, lon, share, COAST_KM[name]) for name in WEIGHTS
              if (site / name / "map.json").exists()}
    if not models:
        raise SystemExit("No model maps to blend")

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    times = [now + timedelta(hours=h) for h in list(range(0, 72)) + list(range(72, 241, 3))]
    if out.exists():
        shutil.rmtree(out)
    run_name = now.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True)
    land = site / "map" / "land.png"
    if land.exists():
        shutil.copy(land, out / "land.png")
    else:
        maplib.save_land(out / "land.png", metnordic_map.land_fraction(lat, lon))

    frames, used = [], set()
    for when in times:
        t = int(when.timestamp())
        total = np.zeros(lat.shape, np.float32)
        speed = np.zeros(lat.shape, np.float32)
        gust = np.zeros(lat.shape, np.float32)
        east = np.zeros(lat.shape, np.float32)
        north = np.zeros(lat.shape, np.float32)
        here = []
        for name, model in models.items():
            values = model.at(t)
            if values is None or not values[4].any():
                continue
            here.append(name)
            u, v, s, g, inside, taper = values
            w = WEIGHTS[name] * inside * taper
            # Direction weight: share x speed, calm counted as 1 m/s.
            unit = np.maximum(np.hypot(u, v), 1e-6)
            pull = w * np.maximum(s, 1.0)
            east += pull * u / unit
            north += pull * v / unit
            total += w
            speed += w * s
            gust += w * g
            used.add(name)
        if not total.any():
            continue
        valid = total > 0
        safe = np.where(valid, total, 1)
        maplib.save_frame(folder / f"{t}.png", speed / safe, maplib.direction_from(east, north), gust / safe, valid)
        # Which models are in this hour, for the app's caption ("Blend · ICON-EU, ECMWF, GFS").
        frames.append({"time": t, "file": f"{run_name}/{t}.png", "models": [NAMES[name] for name in WEIGHTS if name in here]})
    names = ", ".join(NAMES[name] for name in WEIGHTS if name in used)
    manifest = maplib.write_manifest(out, f"Blend · {len(used)} models",
                                     f"Mixed from these maps: {names} (see their credits)",
                                     now, (SOUTH, NORTH, WEST, EAST), width, height, frames,
                                     gust_note="gust m/s x 5 (weighted mean of the models' gusts)")
    manifest["models"] = {name: WEIGHTS[name] for name in WEIGHTS if name in used}
    (out / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"Blend of {names}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
