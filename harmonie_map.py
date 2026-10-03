"""Wind map frames for the Latvian coast from FMI's HARMONIE 2.5 km (FMI open data).

FMI runs HARMONIE every 3 hours, about 66 hours ahead, and its open data service cuts
the grid to a box and converts it to latitude/longitude on request (one GRIB2 file of
about 20 MB for 10 m wind, direction components and gusts). This takes the newest
run, interpolates it to the same Web Mercator pixels as the MET Nordic map (so it
shares that map's land mask) and writes one PNG per hour plus a manifest, in the same
format as the other maps (see maplib.py).

    python harmonie_map.py site/harmonie
"""
import re
import sys
import tempfile
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import eccodes as ec
import numpy as np

import maplib
import metnordic_map

WFS = ("https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature"
       "&storedquery_id=fmi::forecast::harmonie::surface::grid&parameters=WindUMS,WindVMS,WindGust"
       "&format=grib2&timestep=60&bbox={west},{south},{east},{north}")
# The same box and pixels as the MET Nordic map.
SOUTH, NORTH, WEST, EAST = metnordic_map.SOUTH, metnordic_map.NORTH, metnordic_map.WEST, metnordic_map.EAST


def get(url, timeout=600):
    request = urllib.request.Request(url, headers={"User-Agent": maplib.USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply.read()


def newest_file():
    """The download link of the newest run FMI offers for the box, and its run time."""
    listing = get(WFS.format(west=WEST, south=SOUTH, east=EAST, north=NORTH)).decode()
    links = [link.replace("&amp;", "&") for link in re.findall(r"<gml:fileReference>([^<]+)", listing)]
    if not links:
        raise SystemExit("FMI offers no HARMONIE run")
    runs = [(re.search(r"origintime=([0-9T:\-]+Z)", link).group(1), link) for link in links]
    origin, link = max(runs)
    return link, datetime.strptime(origin, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read(path):
    """{valid unix time: {"10u"/"10v"/"10fg": values}} and the grid description."""
    fields, grid = defaultdict(dict), None
    with open(path, "rb") as f:
        while (gid := ec.codes_grib_new_from_file(f)) is not None:
            try:
                if grid is None:
                    grid = {key: ec.codes_get(gid, key) for key in (
                        "Ni", "Nj", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                        "iDirectionIncrementInDegrees", "jDirectionIncrementInDegrees", "jScansPositively")}
                date, hhmm = ec.codes_get(gid, "validityDate"), ec.codes_get(gid, "validityTime")
                t = int(datetime.strptime(f"{date}{hhmm:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp())
                values = ec.codes_get_values(gid).reshape(grid["Nj"], grid["Ni"]).astype(np.float32)
                fields[t][ec.codes_get(gid, "shortName")] = values
            finally:
                ec.codes_release(gid)
    return fields, grid


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/harmonie")
    link, run_time = newest_file()
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, metnordic_map.KM_PER_PIXEL)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "harmonie.grib2")
        path.write_bytes(get(link))
        fields, grid = read(path)
    dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
    sample = maplib.regular_sampler(lat, lon, grid["latitudeOfFirstGridPointInDegrees"],
                                    grid["longitudeOfFirstGridPointInDegrees"], dlat,
                                    grid["iDirectionIncrementInDegrees"], grid["Ni"], grid["Nj"])
    out.mkdir(parents=True, exist_ok=True)
    maplib.save_land(out / "land.png", metnordic_map.land_fraction(lat, lon))
    share = maplib.land_share(lat, lon)
    run_name = run_time.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for t in sorted(fields):
        step = fields[t]
        if not {"10u", "10v", "10fg"} <= step.keys() or t < run_time.timestamp():
            continue
        u, v = sample(step["10u"]), sample(step["10v"])
        maplib.save_frame(folder / f"{t}.png", np.hypot(u, v), maplib.direction_from(u, v), sample(step["10fg"]),
                          sample.valid, coast=(share, 3.75, metnordic_map.KM_PER_PIXEL))
        frames.append({"time": t, "file": f"{run_name}/{t}.png"})
    manifest = maplib.write_manifest(out, "HARM-FI 2.5 km",
                                     "FMI, HARMONIE forecast, CC BY 4.0: https://en.ilmatieteenlaitos.fi/open-data",
                                     run_time, (SOUTH, NORTH, WEST, EAST), width, height, frames)
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"HARMONIE run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
