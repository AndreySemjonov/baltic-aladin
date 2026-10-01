"""Wind map frames for the southern Latvian coast from ALADIN 2.3 km (ČHMÚ open data).

Uses the GRIB files extract.py downloads (set ALADIN_KEEP to a folder when running it),
or downloads the newest run itself. ALADIN's domain ends at about 57.0-57.2°N here, so
the frames carry an alpha channel: 0 north of the domain, where the app shows no wind.
Same pixels size and format as the other maps (see maplib.py).

    ALADIN_KEEP=aladin-files python extract.py
    python aladin_map.py site/aladin aladin-files
"""
import math
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import eccodes as ec
import numpy as np

import extract
import maplib
import metnordic_map

# Kurzeme to Riga and Jūrmala; north of about 57.0°N is outside ALADIN and left empty.
SOUTH, NORTH, WEST, EAST = 55.6, 57.25, 20.4, 25.4


def lambert(lat, lon, grid):
    """Degrees -> the grid's fractional column and row (Lambert conformal on a sphere)."""
    radius = grid["radius"]
    lat1 = math.radians(grid["Latin1InDegrees"])
    lat2 = math.radians(grid["Latin2InDegrees"])
    lat0 = math.radians(grid["LaDInDegrees"])
    lon0 = math.radians(grid["LoVInDegrees"])
    if abs(lat1 - lat2) < 1e-9:
        n = math.sin(lat1)
    else:
        n = math.log(math.cos(lat1) / math.cos(lat2)) / math.log(math.tan(math.pi / 4 + lat2 / 2) / math.tan(math.pi / 4 + lat1 / 2))
    f = math.cos(lat1) * math.tan(math.pi / 4 + lat1 / 2) ** n / n
    rho0 = radius * f / math.tan(math.pi / 4 + lat0 / 2) ** n

    def project(la, lo):
        rho = radius * f / np.tan(np.pi / 4 + np.radians(la) / 2) ** n
        theta = n * (np.radians(lo) - lon0)
        return rho * np.sin(theta), rho0 - rho * np.cos(theta)

    first_lon = grid["longitudeOfFirstGridPointInDegrees"]
    x0, y0 = project(grid["latitudeOfFirstGridPointInDegrees"], first_lon - 360 if first_lon > 180 else first_lon)
    x, y = project(lat, lon)
    return (x - x0) / grid["DxInMetres"], (y - y0) / grid["DyInMetres"]


def read(path, window):
    """{valid unix time: values in the window} for one variable file."""
    (r0, r1), (c0, c1) = window
    out = {}
    with open(path, "rb") as f:
        while (gid := ec.codes_grib_new_from_file(f)) is not None:
            try:
                date, hhmm = ec.codes_get(gid, "validityDate"), ec.codes_get(gid, "validityTime")
                t = int(datetime.strptime(f"{date}{hhmm:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp())
                nx, ny = ec.codes_get(gid, "Nx"), ec.codes_get(gid, "Ny")
                out[t] = ec.codes_get_values(gid).reshape(ny, nx)[r0:r1 + 1, c0:c1 + 1].astype(np.float32)
            finally:
                ec.codes_release(gid)
    return out


def grid_of(path):
    with open(path, "rb") as f:
        gid = ec.codes_grib_new_from_file(f)
        try:
            return {key: ec.codes_get(gid, key) for key in (
                "Nx", "Ny", "DxInMetres", "DyInMetres", "LoVInDegrees", "LaDInDegrees", "Latin1InDegrees",
                "Latin2InDegrees", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                "radius", "jScansPositively")}
        finally:
            ec.codes_release(gid)


def build(out, folder, run):
    run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    files = {key: folder / f"{variable}.grb" for key, variable in extract.VARIABLES.items()}
    grid = grid_of(files["speed"])
    if not grid["jScansPositively"]:
        raise SystemExit("ALADIN grid rows run north to south; not handled")
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, metnordic_map.KM_PER_PIXEL)
    fi, fj = lambert(lat, lon, grid)
    # Only the part of the grid under the box is kept from each field.
    c0, c1 = max(int(np.floor(fi.min())), 0), min(int(np.ceil(fi.max())) + 1, grid["Nx"] - 1)
    r0, r1 = max(int(np.floor(fj.min())), 0), min(int(np.ceil(fj.max())) + 1, grid["Ny"] - 1)
    sample = maplib.Bilinear(fi - c0, fj - r0, c1 - c0 + 1, r1 - r0 + 1)
    # Inside the box but beyond the model's last row or column.
    sample.valid &= (fi <= grid["Nx"] - 1) & (fj <= grid["Ny"] - 1)
    fields = {key: read(path, ((r0, r1), (c0, c1))) for key, path in files.items()}

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    maplib.save_land(out / "land.png", metnordic_map.land_fraction(lat, lon))
    run_name = run_time.strftime("%Y%m%dT%HZ")
    frame_folder = out / run_name
    frame_folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for t in sorted(fields["speed"]):
        if t < run_time.timestamp() or any(t not in fields[key] for key in fields):
            continue
        speed, degrees = fields["speed"][t], np.radians(fields["direction"][t])
        # Direction through its components, so 350° and 10° don't average to 180°.
        u, v = sample(-speed * np.sin(degrees)), sample(-speed * np.cos(degrees))
        gust = sample(np.hypot(fields["gust_u"][t], fields["gust_v"][t]))
        maplib.save_frame(frame_folder / f"{t}.png", sample(speed), maplib.direction_from(u, v), gust, sample.valid)
        frames.append({"time": t, "file": f"{run_name}/{t}.png"})
    manifest = maplib.write_manifest(out, "ALADIN 2.3 km",
                                     "ČHMÚ (Czech Hydrometeorological Institute), ALADIN, open data, CC BY 4.0: https://opendata.chmi.cz/",
                                     run_time, (SOUTH, NORTH, WEST, EAST), width, height, frames)
    size = sum(p.stat().st_size for p in frame_folder.glob("*.png"))
    print(f"ALADIN map run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "site/aladin"
    kept = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if kept and (kept / "run.txt").exists():
        build(out, kept, (kept / "run.txt").read_text().strip())
        return
    run = extract.newest_run(datetime.now(timezone.utc))
    if run is None:
        raise SystemExit("No complete ALADIN run found")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for variable in extract.VARIABLES.values():
            extract.download(run, variable, folder)
        build(out, folder, run)


if __name__ == "__main__":
    main()
