"""The Copernicus Baltic wave model's past hours at a few points, for checking it against buoys (the owner, 20.11.2026:
"Yes, compare": is its wave direction off near Pavilosta's coast, as the webcam showed, or was that a one-off?).

    python wave_points.py --points '[["name", lat, lon], ...]' --days 30 --out wave-points

One CSV per point (time_utc, hs, tp, dir, dir_ww, dir_sw1, hs_ww, hs_sw1): the model's hourly analysis
(cmems_mod_bal_wav_anfc_PT1H-i: VHM0, VTPK, VMDR mean way from, the wind sea's and the first swell's), at the nearest
sea cell. Needs our Copernicus login (COPERNICUSMARINE_SERVICE_USERNAME, _PASSWORD). Nothing uploaded.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

DATASET = "cmems_mod_bal_wav_anfc_PT1H-i"
VARS = {"hs": "VHM0", "tp": "VTPK", "dir": "VMDR", "dir_ww": "VMDR_WW", "dir_sw1": "VMDR_SW1", "hs_ww": "VHM0_WW", "hs_sw1": "VHM0_SW1"}
# (The peak's way too where the dataset has it: the buoys give theirs at the peak, VMDR is the mean.)
PEAK = {"dir_peak": "VPED"}


def open_at(cm, variables, lat, lon, start, end):
    return cm.open_dataset(
        dataset_id=DATASET, variables=variables,
        minimum_longitude=lon - 0.06, maximum_longitude=lon + 0.06, minimum_latitude=lat - 0.04, maximum_latitude=lat + 0.04,
        start_datetime=start.strftime("%Y-%m-%dT%H:%M:%S"), end_datetime=end.strftime("%Y-%m-%dT%H:%M:%S"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", required=True)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="wave-points")
    a = ap.parse_args()
    import copernicusmarine

    out = Path(a.out)
    out.mkdir(exist_ok=True)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=a.days)
    for name, lat, lon in json.loads(a.points):
        try:
            want = {**VARS, **PEAK}
            try:
                ds = open_at(copernicusmarine, list(want.values()), lat, lon, start, end)
            except Exception as error:
                print(name, "no peak way:", str(error)[:120])
                want = VARS
                ds = open_at(copernicusmarine, list(want.values()), lat, lon, start, end)
            hs = ds["VHM0"].values  # time, lat, lon
            # The nearest cell with waves at most hours (the buoy may sit by the model's coast).
            ok = np.isfinite(hs).mean(axis=0) > 0.9
            la, lo = np.meshgrid(ds["latitude"].values, ds["longitude"].values, indexing="ij")
            d = np.where(ok, (la - lat) ** 2 + ((lo - lon) * np.cos(np.radians(lat))) ** 2, np.inf)
            i, j = np.unravel_index(np.argmin(d), d.shape)
            times = ds["time"].values.astype("datetime64[s]").astype(str)
            cols = {k: ds[v].values[:, i, j] for k, v in want.items()}
            lines = ["time_utc," + ",".join(want)]
            for t in range(len(times)):
                lines.append(times[t] + "Z," + ",".join("" if not np.isfinite(cols[k][t]) else f"{cols[k][t]:.2f}" for k in want))
            (out / f"{name}.csv").write_text("\n".join(lines) + "\n")
            print(name, f"cell {la[i, j]:.4f},{lo[i, j]:.4f}", len(times), "hours")
        except Exception as error:
            print(name, "failed:", error)


if __name__ == "__main__":
    main()
