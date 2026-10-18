"""Europe's last models the spot pages read from Open-Meteo (the owner, 18.10.2026), from Open-Meteo's
open data on AWS (CC BY 4.0, not its API), each made into our wind tiles (tiles/<set>) and the spots'
values from its own cells (spot_cells.py: points/<set>.json, points/<set>-wx.json), as ukmo_tiles.py
does for the UK Met Office's UKV:

    icon-ch2  MeteoSwiss ICON-CH2, 2.1 km, 5 days: rotated grid (pole 43 N 170 W), 545 x 353 from
              -6.46, -4.06 every 0.02°
    knmi-eu   KNMI HARMONIE-AROME Europe, 5.5 km, 60 hours: rotated grid (pole 35 N 172 E), 676 x 564
              from 13.5, 13.6 every -0.05°
    arome25   Météo-France AROME France 2.5 km, 51 hours: latitude/longitude, 1121 x 717 from 12 W
              37.5 N every 0.025°

The grids worked out from Open-Meteo's projection, its first and last points and the terrain (Mont
Blanc and the Matterhorn where they should be, 18.10.2026); rows from the first point.

    python bucket_tiles.py icon-ch2|knmi-eu|arome25 [--force] [--local DIR] [--hours N]
"""
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np

import quality
import spot_cells
import tiler
import world_tiles

BUCKET = "https://openmeteo.s3.amazonaws.com/"
FADE_KM = 40
SETS = {
    "icon-ch2": {"model": "meteoswiss_icon_ch2", "name": "ICON-CH2 2.1 km", "credit": "ICON-CH2, MeteoSwiss (via Open-Meteo's open data)",
                 "proj": "+proj=ob_tran +o_proj=longlat +o_lat_p=43 +o_lon_p=0 +lon_0=10 +R=6371229 +no_defs",
                 "x0": -6.46, "y0": -4.06, "dx": 0.02, "dy": 0.02, "nx": 545, "ny": 353, "km": 2.1, "zooms": range(4, 7),
                 "every": 6, "files": 115, "box": (42.0, 50.5, 0.5, 17.5)},
    "knmi-eu": {"model": "knmi_harmonie_arome_europe", "name": "HARMONIE Europe 5.5 km",
                "credit": "HARMONIE-AROME Europe, KNMI (via Open-Meteo's open data)",
                "proj": "+proj=ob_tran +o_proj=longlat +o_lat_p=-35 +o_lon_p=0 +lon_0=-188 +R=6371229 +no_defs",
                "x0": 13.5, "y0": 13.6, "dx": -0.05, "dy": -0.05, "nx": 676, "ny": 564, "km": 5.5, "zooms": range(3, 6),
                "every": 3, "files": 55, "box": (37.0, 67.5, -27.0, 46.0)},
    "arome25": {"model": "meteofrance_arome_france0025", "key": "meteofrance_arome_france", "name": "AROME France 2.5 km",
                "credit": "AROME, Météo-France (via Open-Meteo's open data)", "proj": None,
                "x0": -12.0, "y0": 37.5, "dx": 0.025, "dy": 0.025, "nx": 1121, "ny": 717, "km": 2.5, "zooms": range(4, 7),
                "every": 3, "files": 45, "box": (37.5, 55.4, -12.0, 16.0)},
}


class Grid:
    def __init__(self, spec):
        self.spec = spec
        self.to_grid = None
        if spec["proj"]:
            from pyproj import CRS, Transformer
            self.to_grid = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_proj4(spec["proj"]), always_xy=True)

    def place(self, lon, lat):
        """Column, row and weight (1 inside, falling to 0 over the last FADE_KM to the edge)."""
        s = self.spec
        lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
        x, y = self.to_grid.transform(lon, lat) if self.to_grid else (lon, lat)
        col, row = (np.asarray(x) - s["x0"]) / s["dx"], (np.asarray(y) - s["y0"]) / s["dy"]
        nx, ny = s["nx"], s["ny"]
        inside = (col >= 0) & (row >= 0) & (col <= nx - 1) & (row <= ny - 1)
        cells = np.minimum(np.minimum(col, nx - 1 - col), np.minimum(row, ny - 1 - row))
        return col, row, np.where(inside, np.clip(cells * s["km"] / FADE_KM, 0, 1), 0)

    def sample(self, values, col, row):
        nx, ny = self.spec["nx"], self.spec["ny"]
        c = np.clip(col, 0, nx - 1.001)
        r = np.clip(row, 0, ny - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        top = values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc
        bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr


def newest_run(spec):
    """The newest run at the set's hours (every 3 or 6 hours from 00 UTC) with its hours there."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // spec["every"] * spec["every"], minute=0, second=0, microsecond=0)
    for back in range(6):
        run = start - timedelta(hours=spec["every"] * back)
        keys = sorted(k for k in world_tiles.s3_list(f"data_spatial/{spec['model']}/{run.strftime('%Y/%m/%d/%H%MZ/')}") if k.endswith(".om"))
        if len(keys) >= spec["files"]:
            return run, keys
    raise SystemExit(f"{spec['model']}: no complete run")


def main():
    import omfiles
    args = sys.argv[1:]
    if not args or args[0] not in SETS:
        raise SystemExit(__doc__)
    name, spec = args[0], SETS[args[0]]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    run, keys = newest_run(spec)
    out = tiler.Run(name, run, spec["zooms"], spec["name"], spec["credit"], "--force" in args, local)
    if not out.wanted:
        print(f"{name} {run:%Y%m%dT%HZ} already published")
        return
    if limit:
        keys = keys[:limit]
    grid = Grid(spec)
    started = time.time()
    placement = tiler.Placement(tiler.tiles_in_box(spec["box"], spec["zooms"]), grid.place)
    try:
        cells = spot_cells.SpotCells(name, spec.get("key", spec["model"]), weather=f"{name}-wx")
    except Exception as error:
        print(f"{name}: the spots' cells left out ({error})", flush=True)
        cells = None
    print(f"{name} {run:%Y%m%dT%HZ}: {len(placement.parts)} tiles, {len(keys)} hours", flush=True)
    for key in keys:
        valid = datetime.strptime(key.rsplit("/", 1)[-1][:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
        t = int(valid.timestamp())
        handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
        handle.close()
        data = world_tiles.fetch_bytes(BUCKET + key)
        if data is None:
            continue
        with open(handle.name, "wb") as file:
            file.write(data)
        reader = omfiles.OmFileReader.from_path(handle.name)

        def field(variable):
            try:
                return reader.get_child_by_name(variable).read_array((slice(None), slice(None))).astype(np.float32)
            except Exception:
                return None
        east, north = field("wind_u_component_10m"), field("wind_v_component_10m")
        if east is None or north is None:
            speed, direction = field("wind_speed_10m"), field("wind_direction_10m")
            if speed is not None and direction is not None:
                radians = np.radians(np.nan_to_num(direction))
                east, north = -np.nan_to_num(speed) * np.sin(radians), -np.nan_to_num(speed) * np.cos(radians)
        gust, air, rain, cloud = field("wind_gusts_10m"), field("temperature_2m"), field("precipitation"), field("cloud_cover")
        reader.close()
        os.unlink(handle.name)
        if east is None or north is None:
            print(f"  {valid:%d %H:%M}: no wind", flush=True)
            continue
        valid_mask = np.isfinite(east) & np.isfinite(north)
        east, north = np.nan_to_num(east), np.nan_to_num(north)
        speed = np.hypot(east, north)
        gust = np.maximum(np.nan_to_num(gust), speed) if gust is not None else speed
        if cells is not None:
            cells.add_placed(t, grid.place, grid.sample, east, north, gust, air=air, rain=rain, cloud=cloud)
        if not out.checked(spec["name"], t, quality.wind(east, north, gust)):
            continue
        valid_values = valid_mask.astype(np.float32)
        out.put(t, tiler.pack(placement, grid.sample, lambda at: tiler.wind_rgb(at(east), at(north), at(gust)),
                              valid=lambda at: at(valid_values) > 0.5))
        print(f"  {valid:%d %H:%M}: {out.bytes / 1e6:.0f} MB, {time.time() - started:.0f} s", flush=True)
    out.finish()
    if cells is not None:
        cells.publish(out.folder, local and local + "/points")
    print(f"{name} done in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up: omfiles and pyproj together crashed Python's exit after the
    # work was done ("free(): invalid pointer", 18.10.2026), the job marked failed.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
