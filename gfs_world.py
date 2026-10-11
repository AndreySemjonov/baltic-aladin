"""Wind map frames for the whole world from NOAA's GFS 0.25° (public domain), 4 days.

The quick world layer (the owner, 12.10.2026, before a trip to Seoul): the wind and gusts
everywhere between 78°S and 78°N, every 3 hours for 4 days, about 40 km per pixel on Web
Mercator, in the Baltic maps' format (maplib.py), so the app shows it as one more wind map. The
land mask is GFS's own. The proper world map (tiles on R2, finer, ECMWF too) comes later.

    python gfs_world.py site/world          newest complete run
    python gfs_world.py --check             print the newest complete run (YYYYMMDDHH)
"""
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

import numpy as np

import gfs_map
import maplib

SOUTH, NORTH, WEST, EAST = -78.0, 78.0, -180.0, 180.0
KM_PER_PIXEL = 40
STEPS = list(range(0, 97, 3))
# The whole globe (no subregion): the wind at 10 m, the surface gust and the land mask.
FILTER = ("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?dir=%2Fgfs.{day}%2F{hour}%2Fatmos"
          "&file=gfs.t{hour}z.pgrb2.0p25.f{step:03d}&var_UGRD=on&var_VGRD=on&var_GUST=on&var_LAND=on"
          "&lev_10_m_above_ground=on&lev_surface=on")


def fetch(run, step):
    url = FILTER.format(day=run.strftime("%Y%m%d"), hour=run.strftime("%H"), step=step)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(gfs_map.request(url), timeout=180) as reply:
                return reply.read()
        except urllib.error.URLError:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def main():
    if sys.argv[1:] == ["--check"]:
        print(gfs_map.newest_run().strftime("%Y%m%d%H"))
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/world")
    run = gfs_map.newest_run()
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, KM_PER_PIXEL)
    # GFS's longitudes run 0-360 from Greenwich: the western half shifted round.
    lon = np.mod(lon, 360.0)
    out.mkdir(parents=True, exist_ok=True)
    run_name = run.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames, sample, land_saved = [], None, False
    for step in STEPS:
        fields, grid = gfs_map.decode(fetch(run, step))
        time.sleep(0.5)  # NOMADS asks for a gentle pace
        if "10u" not in fields or "10v" not in fields:
            continue
        if sample is None:
            dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
            sample = maplib.regular_sampler(lat, lon, grid["latitudeOfFirstGridPointInDegrees"],
                                            grid["longitudeOfFirstGridPointInDegrees"] % 360, dlat,
                                            grid["iDirectionIncrementInDegrees"], grid["Ni"], grid["Nj"])
        if not land_saved and "lsm" in fields:
            maplib.save_land(out / "land.png", sample(fields["lsm"]))
            land_saved = True
        u, v = sample(fields["10u"]), sample(fields["10v"])
        speed = np.hypot(u, v)
        gust = sample(fields["gust"]) if "gust" in fields else speed
        t = int((run + timedelta(hours=step)).timestamp())
        maplib.save_frame(folder / f"{t}.png", speed, maplib.direction_from(u, v), gust)
        frames.append({"time": t, "file": f"{run_name}/{t}.png"})
    if not land_saved:
        # No land field this run: all sea (the colours then the same strength everywhere).
        maplib.save_land(out / "land.png", np.zeros((height, width)))
    manifest = maplib.write_manifest(out, "GFS 0.25° world", "NOAA NCEP, GFS (public domain): https://nomads.ncep.noaa.gov/",
                                     run, (SOUTH, NORTH, WEST, EAST), width, height, frames,
                                     gust_note="gust m/s x 5 (surface gust)")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"GFS world run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
