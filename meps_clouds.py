"""MEPS's own clouds in layers as map tiles (the owner, 01.11.2026, the forecast clouds: "more structure on clouds and
clear difference between low/mid/high clouds and when they move with different speed"; invented texture on ICON-EU's
smooth 7 km layers kept looking invented, "Yes, option 2"). MET Norway's MEPS deterministic run (2.5 km, the Nordic
countries and the Baltic; thredds mepslatest, OPeNDAP, CC BY 4.0) has low, medium and high cloud of its own: cloud
streets, showery cells, the gaps behind fronts. Hourly to 66 hours, tiles/meps-cloud as ICON-EU's cloud layers
(<time>-cloud.pack: tiler.cloud_rgb, low, mid, high x 255; alpha its weight, falling over the last 50 km to its
edge), zooms 3-5: the app's and the dev map's cloud layers, MEPS first, ICON-EU where it isn't.

    python meps_clouds.py [--force] [--local DIR] [--hours N]
"""
import re
import sys
import urllib.request
from datetime import datetime, timezone

import numpy as np

import tiler

THREDDS = "https://thredds.met.no/thredds/"
ZOOMS = range(3, 6)
FADE_KM = 50
NAMES = ("low_type_cloud_area_fraction", "medium_type_cloud_area_fraction", "high_type_cloud_area_fraction")
CREDIT = "MEPS, MET Norway (CC BY 4.0)"


def runs():
    """The deterministic runs MET Norway lists now, newest last."""
    with urllib.request.urlopen(THREDDS + "catalog/mepslatest/catalog.html", timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    return sorted(set(re.findall(r"meps_det_2_5km_(\d{8}T\d{2})Z\.ncml", html)))


def main():
    import xarray as xr
    from pyproj import Transformer
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    force = "--force" in args
    # The newest run whose hours are all there (a run is listed while it's still being written).
    data = stamp = None
    for name in reversed(runs()[-3:]):
        d = xr.open_dataset(f"{THREDDS}dodsC/mepslatest/meps_det_2_5km_{name}Z.ncml")
        # And its last hour written (not yet: NaN), so a half-made run's end isn't published as clear sky.
        if d.sizes["time"] >= (limit or 60) and np.isfinite(d[NAMES[0]].isel(time=(limit or d.sizes["time"]) - 1).values).any():
            data, stamp = d, name
            break
    if data is None:
        raise SystemExit("MEPS: no run with its hours yet")
    run_time = datetime.strptime(stamp, "%Y%m%dT%H").replace(tzinfo=timezone.utc)
    out = tiler.Run("meps-cloud", run_time, ZOOMS, "MEPS 2.5 km (clouds in layers)", CREDIT, force,
                    local and f"{local}/meps-cloud")
    if not out.wanted:
        print(f"MEPS {stamp} already published")
        return
    x, y = data["x"].values, data["y"].values
    p = data["projection_lambert"].attrs
    lcc = (f"+proj=lcc +lat_0={p['latitude_of_projection_origin']} +lon_0={p['longitude_of_central_meridian']} "
           f"+lat_1={p['standard_parallel'][0]} +lat_2={p['standard_parallel'][1]} +R={p['earth_radius']} +units=m +no_defs")
    to = Transformer.from_crs("EPSG:4326", lcc, always_xy=True)
    km = abs(float(x[1] - x[0])) / 1000

    def place(lon, lat):
        gx, gy = to.transform(lon, lat)
        col, row = (gx - x[0]) / (x[1] - x[0]), (gy - y[0]) / (y[1] - y[0])
        inside = (col >= 0) & (row >= 0) & (col <= len(x) - 1) & (row <= len(y) - 1)
        cells = np.minimum(np.minimum(col, len(x) - 1 - col), np.minimum(row, len(y) - 1 - row))
        return col, row, np.where(inside, np.clip(cells * km / FADE_KM, 0, 1), 0)

    # Coarse zooms average the cells under each pixel (tiler.pack's km: at zoom 3 about 4 cells across, at 4 two).
    def sample(values, col, row):
        c = np.clip(col, 0, len(x) - 1.001)
        r = np.clip(row, 0, len(y) - 1.001)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        top = values[r0, c0] * (1 - fc) + values[r0, c0 + 1] * fc
        bottom = values[r0 + 1, c0] * (1 - fc) + values[r0 + 1, c0 + 1] * fc
        return top * (1 - fr) + bottom * fr

    lat, lon = data["latitude"].values, data["longitude"].values
    box = (float(np.nanmin(lat)), float(np.nanmax(lat)), float(np.nanmin(lon)), float(np.nanmax(lon)))
    placement = tiler.Placement(tiler.tiles_in_box(box, ZOOMS), place)
    times = data["time"].values[:limit] if limit else data["time"].values
    print(f"MEPS {stamp}: {len(times)} hours, {len(placement.parts)} tiles over {tuple(round(v, 1) for v in box)}", flush=True)
    for k, when in enumerate(times):
        t = int(when.astype("datetime64[s]").astype(int))
        low, mid, high = (np.nan_to_num(data[n].isel(time=k).values.squeeze()) for n in NAMES)
        if out.checked("MEPS", t, None if max(low.max(), mid.max(), high.max()) <= 1.01 else "cloud over 100 %"):
            out.put(t, tiler.pack(placement, sample, lambda at: tiler.cloud_rgb(at(low), at(mid), at(high)), km=km), "cloud")
    out.finish(area={"south": box[0], "north": box[1], "west": box[2], "east": box[3]})
    print(f"MEPS {stamp}: done")


if __name__ == "__main__":
    main()
