"""Wind map frames for the whole Baltic from NOAA's GFS 0.25° (public domain), 16 days.

NOAA's NOMADS server cuts GFS to a box and to the fields asked for (about 20 KB per
step), so a run is 129 small downloads: every 3 hours to 384 hours. Same box as the
ICON-EU map, about 7 km per pixel (the model's grid is about 15 x 28 km here); format in
maplib.py. The land mask is ICON-EU's.

    python gfs_map.py site/gfs              newest complete run
    python gfs_map.py --check               print the newest complete run (YYYYMMDDHH)
"""
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes as ec
import numpy as np

import iconeu_map
import maplib

SOUTH, NORTH, WEST, EAST = iconeu_map.SOUTH, iconeu_map.NORTH, iconeu_map.WEST, iconeu_map.EAST
KM_PER_PIXEL = 7
STEPS = list(range(0, 385, 3))
PROD = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
FILTER = ("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?dir=%2Fgfs.{day}%2F{hour}%2Fatmos"
          "&file=gfs.t{hour}z.pgrb2.0p25.f{step:03d}&var_UGRD=on&var_VGRD=on&var_GUST=on"
          "&lev_10_m_above_ground=on&lev_surface=on&subregion="
          f"&toplat={NORTH}&leftlon={WEST}&rightlon={EAST}&bottomlat={SOUTH}")


def request(url, method="GET"):
    return urllib.request.Request(url, method=method, headers={"User-Agent": maplib.USER_AGENT})


def newest_run():
    """The newest 00/06/12/18 UTC run whose last step is published."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for back in range(6):
        run = start - timedelta(hours=6 * back)
        day, hour = run.strftime("%Y%m%d"), run.strftime("%H")
        try:
            with urllib.request.urlopen(request(f"{PROD}/gfs.{day}/{hour}/atmos/gfs.t{hour}z.pgrb2.0p25.f384", "HEAD"),
                                        timeout=60):
                return run
        except urllib.error.URLError:
            continue
    raise SystemExit("No complete GFS run found")


def fetch(run, step):
    url = FILTER.format(day=run.strftime("%Y%m%d"), hour=run.strftime("%H"), step=step)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request(url), timeout=120) as reply:
                return reply.read()
        except urllib.error.URLError:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))


def decode(data):
    """{"10u"/"10v"/"gust": values} and the grid of one step's file."""
    fields, grid = {}, None
    for message in maplib.grib_messages(data):
        gid = ec.codes_new_from_message(message)
        try:
            grid = grid or {key: ec.codes_get(gid, key) for key in (
                "Ni", "Nj", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees", "jScansPositively")}
            fields[ec.codes_get(gid, "shortName")] = ec.codes_get_values(gid).reshape(grid["Nj"], grid["Ni"])
        finally:
            ec.codes_release(gid)
    return fields, grid


def main():
    if sys.argv[1:] == ["--check"]:
        print(newest_run().strftime("%Y%m%d%H"))
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/gfs")
    run = newest_run()
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, KM_PER_PIXEL)
    out.mkdir(parents=True, exist_ok=True)
    maplib.save_land(out / "land.png", iconeu_map.land_fraction(lat, lon))
    run_name = run.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames, sample = [], None
    for step in STEPS:
        fields, grid = decode(fetch(run, step))
        time.sleep(0.5)  # NOMADS asks for a gentle pace
        if "10u" not in fields or "10v" not in fields:
            continue
        if sample is None:
            dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
            sample = maplib.regular_sampler(lat, lon, grid["latitudeOfFirstGridPointInDegrees"],
                                            grid["longitudeOfFirstGridPointInDegrees"], dlat,
                                            grid["iDirectionIncrementInDegrees"], grid["Ni"], grid["Nj"])
        u, v = sample(fields["10u"]), sample(fields["10v"])
        speed = np.hypot(u, v)
        gust = sample(fields["gust"]) if "gust" in fields else speed
        t = int((run + timedelta(hours=step)).timestamp())
        maplib.save_frame(folder / f"{t}.png", speed, maplib.direction_from(u, v), gust)
        frames.append({"time": t, "file": f"{run_name}/{t}.png"})
    manifest = maplib.write_manifest(out, "GFS 0.25°", "NOAA NCEP, GFS (public domain): https://nomads.ncep.noaa.gov/",
                                     run, (SOUTH, NORTH, WEST, EAST), width, height, frames,
                                     gust_note="gust m/s x 5 (surface gust)")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"GFS run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
