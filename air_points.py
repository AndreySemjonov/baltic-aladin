"""Air quality and pollen from CAMS (the owner, 18.10.2026: off Open-Meteo's API; Copernicus
Atmosphere Monitoring Service, from Open-Meteo's open data on AWS, CC BY 4.0, not its API): each
run's hours as files for our server (station-history /air) to read a place's every hour in one
range request: CAMS Europe (0.1°, 4 days: the pollutants and the six pollens) and CAMS global (0.4°,
5 days: the pollutants and the UV index).

    air/<region>/<run>/<band>.bin   rows `band` x cols points, each point every variable's every
                                    hour as little-endian uint16 (the value x its scale; 65535 none)
    air/<region>/index.json         {run, times, vars, scales, grid {lat0, lon0, dlat, dlon, rows,
                                    cols}, band, record (bytes a point)}, last

    python air_points.py [europe|global] [--force] [--local DIR]
"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import spot_points
import world_tiles

BUCKET = "https://openmeteo.s3.amazonaws.com/"
FILES = "https://files.meteogust.com/"
REGIONS = {
    "europe": ("cams_europe", ["pm2_5", "pm10", "ozone", "nitrogen_dioxide", "sulphur_dioxide", "alder_pollen", "birch_pollen",
                               "grass_pollen", "mugwort_pollen", "olive_pollen", "ragweed_pollen"]),
    "global": ("cams_global", ["pm2_5", "pm10", "ozone", "nitrogen_dioxide", "sulphur_dioxide", "uv_index"]),
}
# The pollutants to 0.1 µg/m³, the pollen in whole grains a cubic metre, the UV index to 0.1.
SCALES = {"alder_pollen": 1, "birch_pollen": 1, "grass_pollen": 1, "mugwort_pollen": 1, "olive_pollen": 1, "ragweed_pollen": 1}
MAX_FILE = 80_000_000


def newest_run(model):
    latest = json.loads(spot_points.get(BUCKET + f"data_spatial/{model}/latest.json?t={int(time.time())}"))
    run = datetime.strptime(latest["reference_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return run, latest


def make(region, force=False, local=None):
    import omfiles
    model, names = REGIONS[region]
    run, latest = newest_run(model)
    run_name = run.strftime("%Y%m%dT%HZ")
    before = spot_points.get(FILES + f"air/{region}/index.json?t={int(time.time())}")
    if not force and local is None and before and json.loads(before).get("run") == run_name:
        print(f"{region} {run_name}: published already", flush=True)
        return
    keys = [k for k in world_tiles.s3_list(f"data_spatial/{model}/{run.strftime('%Y/%m/%d/%H%MZ/')}") if k.endswith(".om")]
    keys.sort()
    times = [int(datetime.strptime(k.rsplit("/", 1)[-1][:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc).timestamp()) for k in keys]
    started = time.time()

    def fetch(key):
        handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
        handle.close()
        request = urllib.request.Request(BUCKET + key, headers={"User-Agent": "MeteoGust helper"})
        with urllib.request.urlopen(request, timeout=300) as reply, open(handle.name, "wb") as out:
            out.write(reply.read())
        return handle.name

    with ThreadPoolExecutor(8) as pool:
        paths = list(pool.map(fetch, keys))
    first = omfiles.OmFileReader.from_path(paths[0])
    rows, cols = first.get_child_by_name(names[0]).shape
    box = [float(v) for v in re.search(r"BBOX\[([^\]]+)\]", first.get_child_by_name("crs_wkt").read_scalar()).group(1).split(",")]
    first.close()
    # BBOX[first row's latitude, first column's longitude, last row's, last column's].
    grid = {"lat0": box[0], "lon0": box[1], "dlat": (box[2] - box[0]) / (rows - 1), "dlon": (box[3] - box[1]) / (cols - 1),
            "rows": rows, "cols": cols}
    hours = len(paths)
    # [rows, cols, vars, hours] uint16, filled hour by hour.
    data = np.full((rows, cols, len(names), hours), 65535, np.uint16)
    for h, path in enumerate(paths):
        reader = omfiles.OmFileReader.from_path(path)
        for v, name in enumerate(names):
            try:
                values = reader.get_child_by_name(name).read_array((slice(None), slice(None)))
            except Exception:
                continue
            scaled = np.rint(np.nan_to_num(values, nan=-1) * SCALES.get(name, 10))
            data[:, :, v, h] = np.where(values == values, np.clip(scaled, 0, 65534), 65535).astype(np.uint16)
        reader.close()
        os.unlink(path)
    record = len(names) * hours * 2
    band = max(1, MAX_FILE // (cols * record))
    files = 0
    for b, start in enumerate(range(0, rows, band)):
        body = data[start:start + band].tobytes()
        name = f"{run_name}/{b}.bin"
        if local:
            (Path(local) / region / run_name).mkdir(parents=True, exist_ok=True)
            (Path(local) / region / name).write_bytes(body)
        else:
            world_tiles.upload(f"air/{region}/{name}", body)
        files += 1
    index = {"run": run_name, "model": model, "times": times, "vars": names, "scales": [SCALES.get(n, 10) for n in names],
             "grid": grid, "band": band, "record": record, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
             "credit": "CAMS, Copernicus Atmosphere Monitoring Service (via Open-Meteo's open data, CC BY 4.0)"}
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (Path(local) / region / "index.json").write_bytes(body)
    else:
        world_tiles.upload(f"air/{region}/index.json", body)
    print(f"{region} {run_name}: {hours} hours, {rows}x{cols}, {files} files, {data.nbytes / 1e6:.0f} MB, {time.time() - started:.0f} s",
          flush=True)


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    chosen = [a for a in args if a in REGIONS]
    for region in chosen or list(REGIONS):
        try:
            make(region, "--force" in args, local)
        except Exception as error:
            print(f"{region}: {error}", flush=True)


if __name__ == "__main__":
    main()
