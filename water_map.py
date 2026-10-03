"""Water map frames: sea level, water temperature and the surface current, hour by hour for the
NEMO-EST run's 5 days, over Lithuania, Latvia and Estonia on the regional maps' Web Mercator
pixels.

Two models: NEMO-EST (Estonian Environment Agency and TalTech, about 1 km, Estonia and the
Gulf of Riga; CC BY 4.0) wherever it reaches, and the Copernicus Marine Baltic model (about
1.7 km, the whole Baltic; copernicus.py, when the helper has a login) elsewhere, crossfaded
over BLEND_KM inside NEMO's edge.

Heights: each model counts from its own zero, so each is shifted by its median difference to
the gauges over the hours both have (docs/gauges.json: Estonian and Latvian gauges on
EH2000 = LAS-2000,5). What is left at each gauge then nudges the map nearby: a correction
that fades out over about NUDGE_KM, kept the same for the whole run.

nemo.py calls `WaterMap` with each NEMO surface file it downloads.

Frames: RGBA PNG, read as raw numbers (no colour management):
    red   = level cm + 128 (1-255); 0 = outside both models (land holds its nearest sea value:
            the app shows the colors through the coastline's sea only)
    green = water temperature °C x 8
    blue  = current speed m/s x 100
    alpha = current direction (towards) degrees x 256/360
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import copernicus
import gauges as all_gauges
import maplib
import metnordic_map

HERE = Path(__file__).parent
OUT = HERE / "water-site"
SOUTH, NORTH, WEST, EAST = metnordic_map.SOUTH, metnordic_map.NORTH, metnordic_map.WEST, metnordic_map.EAST
BLEND_KM = 20
NUDGE_KM = 25


def fill_land(sea):
    """Indices that give every cell its nearest sea cell's value."""
    from scipy import ndimage
    _, (rows, columns) = ndimage.distance_transform_edt(~sea, return_indices=True)
    return rows, columns


class WaterMap:
    def __init__(self, run, lats, lons, sea, nearest_sea_cell):
        from scipy import ndimage
        self.run = run
        (self.lat, self.lon), self.width, self.height = maplib.output_grid(SOUTH, NORTH, WEST, EAST,
                                                                           metnordic_map.KM_PER_PIXEL)
        km = metnordic_map.KM_PER_PIXEL
        # NEMO: land cells take their nearest sea cell's values first, so blending near the
        # coast never mixes in the model's land filler.
        self.nemo_fill = fill_land(sea)
        self.nemo_sample = maplib.regular_sampler(self.lat, self.lon, float(lats[0]), float(lons[0]),
                                                  float(lats[1] - lats[0]), float(lons[1] - lons[0]), len(lons), len(lats))
        self.nemo_cells = {}
        # Copernicus for the rest, the run's hours and a day before (the gauges' hours).
        start = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        self.cop = copernicus.load(SOUTH, NORTH, WEST, EAST, start - timedelta(hours=24), start + timedelta(hours=126))
        if self.cop:
            cop_sea = np.isfinite(self.cop["level"][0])
            self.cop_fill = fill_land(cop_sea)
            clats, clons = self.cop["lats"], self.cop["lons"]
            self.cop_sample = maplib.regular_sampler(self.lat, self.lon, float(clats[0]), float(clons[0]),
                                                     float(clats[1] - clats[0]), float(clons[1] - clons[0]),
                                                     len(clons), len(clats))
            self.cop_index = {t: k for k, t in enumerate(self.cop["times"])}
            self.cop_cells = {}
        # NEMO's weight: 1 well inside its area, fading to 0 over BLEND_KM towards its edge
        # (where Copernicus takes over); without Copernicus, simply its area.
        inside_nemo = self.nemo_sample.valid
        if self.cop:
            distance = ndimage.distance_transform_edt(inside_nemo) * km
            self.nemo_weight = np.clip(distance / BLEND_KM, 0, 1).astype(np.float32)
            self.inside = inside_nemo | self.cop_sample.valid
        else:
            self.nemo_weight = inside_nemo.astype(np.float32)
            self.inside = inside_nemo
        share = maplib.land_share(self.lat, self.lon)
        # Values everywhere inside the models, land too, so the app can cut the colors along
        # the fine coastline; the ranges count only the sea.
        self.water = self.inside & (share < 0.5 if share is not None else True)
        # The gauges, with each model's nearest sea cell and the gauge's map pixel.
        self.gauges = all_gauges.load()
        for gauge in self.gauges:
            found = nearest_sea_cell(gauge["lat"], gauge["lon"], lats, lons, sea)
            if found:
                self.nemo_cells[gauge["id"]] = found[1:]
            if self.cop:
                found = nearest_sea_cell(gauge["lat"], gauge["lon"], self.cop["lats"], self.cop["lons"], cop_sea)
                if found:
                    self.cop_cells[gauge["id"]] = found[1:]
            gauge["pixel"] = (int(np.abs(self.lat[:, 0] - gauge["lat"]).argmin()),
                              int(np.abs(self.lon[0, :] - gauge["lon"]).argmin()))
        self.nemo_offset = None
        self.cop_offset = 0.0
        self.nudge = np.zeros(self.lat.shape, np.float32)
        self.residuals = {}
        self.frames = []
        # The run's lowest and highest level and temperature over the water, for the app's colors.
        self.level_range = [math.inf, -math.inf]
        self.temperature_range = [math.inf, -math.inf]
        self.folder = OUT / start.strftime("%Y%m%dT%HZ")
        self.folder.mkdir(parents=True, exist_ok=True)
        maplib.save_land(OUT / "land.png", share if share is not None else np.zeros(self.lat.shape))

    @staticmethod
    def median_shift(pairs_by_gauge):
        medians = [float(np.median(pairs)) for pairs in pairs_by_gauge.values() if pairs]
        return (round(float(np.median(medians)), 1) if medians else 0.0), len(medians)

    def cop_level(self, t, cell):
        """Copernicus's level (m) in a cell at hour t, or None."""
        if not self.cop or t not in self.cop_index:
            return None
        value = self.cop["level"][self.cop_index[t], cell[0], cell[1]]
        return float(value) if np.isfinite(value) else None

    def calibrate(self, times, ssh):
        """From the run's first NEMO file (its hours overlap the gauges' last 48): each model's
        shift to the gauges, then each gauge's remaining difference and the nudge around it."""
        nemo_pairs, cop_pairs = {}, {}
        for gauge in self.gauges:
            gid, measured = gauge["id"], gauge["levels"]
            # NEMO's shift from the Estonian gauges (one height system, measured on its own coast).
            if gid in self.nemo_cells and gauge["country"] == "EE":
                j, i = self.nemo_cells[gid]
                nemo_pairs[gid] = [measured[t] - float(ssh[k, j, i]) * 100 for k, t in enumerate(times)
                                   if t in measured and abs(ssh[k, j, i]) < 1e10]
            if gid in getattr(self, "cop_cells", {}):
                pairs = []
                for t, value in measured.items():
                    model = self.cop_level(t, self.cop_cells[gid])
                    if model is not None:
                        pairs.append(value - model * 100)
                cop_pairs[gid] = pairs
        self.nemo_offset, n = self.median_shift(nemo_pairs)
        print(f"Water map: NEMO shifted by {self.nemo_offset} cm ({n} gauges)")
        if self.cop:
            self.cop_offset, n = self.median_shift(cop_pairs)
            print(f"Water map: Copernicus shifted by {self.cop_offset} cm ({n} gauges)")
        # What's left at each gauge with both shifts and the blend, then the nudge field.
        sums = np.zeros(self.lat.shape, np.float64)
        weights = np.zeros(self.lat.shape, np.float64)
        km = metnordic_map.KM_PER_PIXEL
        rows, columns = np.indices(self.lat.shape)
        for gauge in self.gauges:
            gid, (pj, pi) = gauge["id"], gauge["pixel"]
            w = float(self.nemo_weight[pj, pi])
            differences = []
            for k, t in enumerate(times):
                if t not in gauge["levels"]:
                    continue
                parts = []
                if w > 0 and gid in self.nemo_cells:
                    j, i = self.nemo_cells[gid]
                    if abs(ssh[k, j, i]) < 1e10:
                        parts.append((w, float(ssh[k, j, i]) * 100 + self.nemo_offset))
                if w < 1 and gid in getattr(self, "cop_cells", {}):
                    model = self.cop_level(t, self.cop_cells[gid])
                    if model is not None:
                        parts.append((1 - w, model * 100 + self.cop_offset))
                if parts:
                    blended = sum(a * b for a, b in parts) / sum(a for a, _ in parts)
                    differences.append(gauge["levels"][t] - blended)
            if not differences:
                continue
            residual = float(np.median(differences))
            self.residuals[gauge["name"]] = round(residual, 1)
            spread = np.exp(-((rows - pj) ** 2 + (columns - pi) ** 2) * km * km / NUDGE_KM ** 2)
            sums += spread * residual
            weights += spread
        # Far from every gauge the nudge fades to nothing (the 0.5 in the denominator).
        self.nudge = (sums / (weights + 0.5)).astype(np.float32)
        print(f"Water map: gauges' remaining differences (cm) {self.residuals}")

    def sample_nemo(self, field):
        values = np.asarray(field, dtype=np.float32)[self.nemo_fill]
        return np.nan_to_num(self.nemo_sample(np.where(np.abs(values) < 1e10, values, 0)))

    def sample_cop(self, field):
        values = np.asarray(field, dtype=np.float32)[self.cop_fill]
        return np.nan_to_num(self.cop_sample(np.where(np.isfinite(values), values, 0)))

    def add_file(self, times, f):
        ssh, sst, ssu, ssv = f["SSH"], f["SST"], f["SSU"], f["SSV"]
        if self.nemo_offset is None:
            self.calibrate(times, ssh)
        for k, t in enumerate(times):
            level = self.sample_nemo(ssh[k]) * 100 + self.nemo_offset
            temperature, u, v = self.sample_nemo(sst[k]), self.sample_nemo(ssu[k]), self.sample_nemo(ssv[k])
            inside = self.nemo_sample.valid
            if self.cop and t in self.cop_index:
                c, w = self.cop_index[t], self.nemo_weight
                level = w * level + (1 - w) * (self.sample_cop(self.cop["level"][c]) * 100 + self.cop_offset)
                temperature = w * temperature + (1 - w) * self.sample_cop(self.cop["temperature"][c])
                u = w * u + (1 - w) * self.sample_cop(self.cop["u"][c])
                v = w * v + (1 - w) * self.sample_cop(self.cop["v"][c])
                inside = self.inside
            level = level + self.nudge
            speed = np.hypot(u, v)
            towards = np.mod(np.degrees(np.arctan2(u, v)), 360)
            pixels = np.stack([np.clip(np.rint(level + 128), 1, 255), np.clip(np.rint(temperature * 8), 0, 255),
                               np.clip(np.rint(speed * 100), 0, 255),
                               np.mod(np.rint(towards * 256 / 360), 256)], axis=-1).astype(np.uint8)
            pixels[~inside] = 0
            frame = {"time": t, "file": f"{self.folder.name}/{t}.png"}
            water = self.water & inside
            if water.any():
                levels = pixels[..., 0][water].astype(np.float32) - 128
                temperatures = pixels[..., 1][water].astype(np.float32) / 8
                # This hour's range, for colors stretched to the day on screen.
                frame["levelRange"] = [float(levels.min()), float(levels.max())]
                frame["temperatureRange"] = [round(float(temperatures.min()), 2), round(float(temperatures.max()), 2)]
                self.level_range = [min(self.level_range[0], float(levels.min())),
                                    max(self.level_range[1], float(levels.max()))]
                self.temperature_range = [min(self.temperature_range[0], float(temperatures.min())),
                                          max(self.temperature_range[1], float(temperatures.max()))]
            Image.fromarray(pixels, "RGBA").save(self.folder / f"{t}.png", optimize=True)
            self.frames.append(frame)

    def finish(self):
        run_time = datetime.strptime(self.run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        model = "NEMO-EST + Copernicus" if self.cop else "NEMO-EST 1 km"
        source = "NEMO-EST, Estonian Environment Agency (Keskkonnaagentuur) and TalTech, CC BY 4.0"
        if self.cop:
            source += "; E.U. Copernicus Marine Service Information (Baltic Sea physics)"
        manifest = maplib.write_manifest(OUT, model, source, run_time, (SOUTH, NORTH, WEST, EAST),
                                         self.width, self.height, self.frames)
        manifest["kind"] = "water"
        manifest["levelOffset"] = self.nemo_offset
        manifest["copernicusOffset"] = self.cop_offset if self.cop else None
        manifest["gaugeDifferences"] = self.residuals
        manifest["landFilled"] = True
        if math.isfinite(self.level_range[0]):
            manifest["levelRange"] = [round(v, 1) for v in self.level_range]
            manifest["temperatureRange"] = [round(v, 2) for v in self.temperature_range]
        manifest["encoding"] = {
            "red": "sea level cm + 128 (EH2000 / LAS-2000,5: the models shifted and nudged to the gauges); "
                   "0 = outside the models; land holds its nearest sea value",
            "green": "water temperature degC x 8", "blue": "current speed m/s x 100",
            "alpha": "current direction (towards) degrees x 256/360"}
        (OUT / "map.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
                                      encoding="utf-8")
        size = sum(p.stat().st_size for p in self.folder.glob("*.png"))
        print(f"Water map {self.run}: {len(self.frames)} frames {self.width}x{self.height}, {size / 1e6:.1f} MB")
