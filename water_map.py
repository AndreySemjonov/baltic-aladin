"""Water map frames from NEMO-EST (Estonian Environment Agency and TalTech, CC BY 4.0):
sea level, water temperature and the surface current, hour by hour for the run's 5 days,
over Estonia and the Gulf of Riga, on the regional maps' Web Mercator pixels.

nemo.py calls `WaterMap` with each surface file it downloads. The model's own zero differs
from the gauges' (EH2000 / LAS-2000,5) by a couple of decimetres, so the levels are shifted
by the Estonian gauges' median difference to the model over the hours both have
(docs/ee-gauges.json), and the shift is written into the manifest.

Frames: RGBA PNG, read as raw numbers (no colour management):
    red   = level cm + 128 (1-255); 0 = no water here (land, or outside the model)
    green = water temperature °C x 8
    blue  = current speed m/s x 100
    alpha = current direction (towards) degrees x 256/360
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import maplib
import metnordic_map

HERE = Path(__file__).parent
OUT = HERE / "water-site"
SOUTH, NORTH, WEST, EAST = 56.9, 60.0, 21.5, 28.4
# The Estonian gauges (as in the agency's observations file) and where they are.
GAUGES = {
    "Häädemeeste": (58.0367, 24.4581), "Pärnu": (58.3846, 24.4852), "Mõntu": (57.9494, 22.1242),
    "Ruhnu": (57.7834, 23.2589), "Virtsu": (58.5727, 23.5136), "Roomassaare": (58.2181, 22.5064),
    "Ristna": (58.9210, 22.0663), "Heltermaa": (58.8642, 23.0437), "Haapsalu sadam": (58.9580, 23.5273),
    "Dirhami": (59.2114, 23.5008), "Paldiski (Põhjasadam)": (59.3497, 24.0475), "Pirita": (59.4688, 24.8208),
    "Loksa": (59.5833, 25.6986), "Kunda": (59.5214, 26.5414), "Narva-Jõesuu": (59.4683, 28.0425),
}


class WaterMap:
    def __init__(self, run, lats, lons, sea, nearest_sea_cell):
        from scipy import ndimage
        self.run = run
        (self.lat, self.lon), self.width, self.height = maplib.output_grid(SOUTH, NORTH, WEST, EAST,
                                                                           metnordic_map.KM_PER_PIXEL)
        # Land cells take the nearest sea cell's values first, so blending near the coast
        # never mixes in the model's land filler.
        _, (self.fill_r, self.fill_c) = ndimage.distance_transform_edt(~sea, return_indices=True)
        self.sample = maplib.regular_sampler(self.lat, self.lon, float(lats[0]), float(lons[0]),
                                             float(lats[1] - lats[0]), float(lons[1] - lons[0]), len(lons), len(lats))
        share = maplib.land_share(self.lat, self.lon)
        self.water = self.sample.valid & (share < 0.5 if share is not None else True)
        # Model cells of the gauges, for the shift to the gauges' heights.
        self.gauge_cells = {}
        for name, (lat, lon) in GAUGES.items():
            found = nearest_sea_cell(lat, lon, lats, lons, sea)
            if found:
                self.gauge_cells[name] = found[1:]
        self.gauge_series = {name: {} for name in self.gauge_cells}
        self.offset = None
        self.frames = []
        self.folder = OUT / datetime.strptime(run, "%Y%m%d%H").strftime("%Y%m%dT%HZ")
        self.folder.mkdir(parents=True, exist_ok=True)
        maplib.save_land(OUT / "land.png", share if share is not None else np.zeros(self.lat.shape))

    def shift_from_gauges(self, times, ssh):
        """The median, over gauges, of (gauge - model) for the hours both have; from the run's
        first file (its hours overlap the gauges' last 48)."""
        try:
            gauges = json.loads((HERE / "docs" / "ee-gauges.json").read_text(encoding="utf-8"))["gauges"]
        except (OSError, ValueError, KeyError):
            gauges = {}
        differences = []
        for name, (j, i) in self.gauge_cells.items():
            hours = gauges.get(name)
            if not hours:
                continue
            measured = {int(hours["start"] + k * hours["step"]): v for k, v in enumerate(hours["level"]) if v is not None}
            pairs = [measured[t] - float(ssh[k, j, i]) * 100 for k, t in enumerate(times)
                     if t in measured and abs(ssh[k, j, i]) < 1e10]
            if pairs:
                differences.append(float(np.median(pairs)))
        self.offset = round(float(np.median(differences)), 1) if differences else 0.0
        print(f"Water map: level shifted by {self.offset} cm ({len(differences)} gauges)")

    def add_file(self, times, f):
        ssh, sst, ssu, ssv = f["SSH"], f["SST"], f["SSU"], f["SSV"]
        if self.offset is None:
            self.shift_from_gauges(times, ssh)
        for k, t in enumerate(times):
            fields = []
            for source in (ssh[k], sst[k], ssu[k], ssv[k]):
                values = np.asarray(source, dtype=np.float32)
                values = values[self.fill_r, self.fill_c]
                fields.append(np.nan_to_num(self.sample(np.where(np.abs(values) < 1e10, values, 0))))
            level, temperature, u, v = fields
            speed = np.hypot(u, v)
            towards = np.mod(np.degrees(np.arctan2(u, v)), 360)
            red = np.clip(np.rint(level * 100 + self.offset + 128), 1, 255)
            pixels = np.stack([np.where(self.water, red, 0),
                               np.clip(np.rint(temperature * 8), 0, 255),
                               np.clip(np.rint(speed * 100), 0, 255),
                               np.mod(np.rint(towards * 256 / 360), 256)], axis=-1).astype(np.uint8)
            pixels[~self.water] = 0
            name = f"{t}.png"
            Image.fromarray(pixels, "RGBA").save(self.folder / name, optimize=True)
            self.frames.append({"time": t, "file": f"{self.folder.name}/{name}"})

    def finish(self):
        run_time = datetime.strptime(self.run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        manifest = maplib.write_manifest(OUT, "NEMO-EST 1 km",
                                         "NEMO-EST, Estonian Environment Agency (Keskkonnaagentuur) and TalTech, CC BY 4.0",
                                         run_time, (SOUTH, NORTH, WEST, EAST), self.width, self.height, self.frames)
        manifest["kind"] = "water"
        manifest["levelOffset"] = self.offset
        manifest["encoding"] = {"red": "sea level cm + 128 (EH2000, the model shifted to the Estonian gauges); 0 = no water",
                                "green": "water temperature degC x 8", "blue": "current speed m/s x 100",
                                "alpha": "current direction (towards) degrees x 256/360"}
        (OUT / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
        size = sum(p.stat().st_size for p in self.folder.glob("*.png"))
        print(f"Water map {self.run}: {len(self.frames)} frames {self.width}x{self.height}, {size / 1e6:.1f} MB")
