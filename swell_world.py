"""The swell for the whole world (the owner, 17.11.2026: "Yes, world swell", after Kitebeach had no swell row: EWAM's
swell covers only Europe's seas and ECMWF's open data has no swell part): Copernicus Marine's global wave model
(MFWAM, 1/12 degree, 3-hourly, about 10 days) as tiles/swell-world at zooms 0-4, in EWAM's swell pack's format
(waves_tiles.swell_hour): red the main swell's height m x 20 + 1 (VHM0_SW1), green its period s x 10 (VTM01_SW1: the
swell part's mean period, close to its peak), blue the wind waves' height m x 20 + 1 (VHM0_WW). The spots' swell rows
read it where EWAM's ends (station-history ourSwell); land within 3 cells of the sea takes the nearest sea cell's.

The same model's total waves too, as tiles/waves-world in the waves' pack (waves_tiles.encode_hour: VHM0, VTPK the peak
period, VMDR): the spots' Waves rows take them where EWAM's end, before ECMWF's, so the total and the swell come from one
model (the owner, 17.11.2026: "take total from Copernicus too outside Europe", after Kitebeach's swell read 1.3 m under
ECMWF's 1.2 m total).

Free data, but Copernicus needs our account (COPERNICUSMARINE_SERVICE_USERNAME and _PASSWORD). Source: E.U.
Copernicus Marine Service Information.

    python swell_world.py [--local DIR] [--hours N]
"""
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

import quality
import tiler
from waves_tiles import encode_hour, filled, regular_grid, swell_hour

DATASET = "cmems_mod_glo_wav_anfc_0.083deg_PT3H-i"
VARIABLES = ("VHM0_SW1", "VTM01_SW1", "VHM0_WW")
TOTAL = ("VHM0", "VTPK", "VMDR")
# The main swell's direction, degrees it comes from (the owner, 17.11.2026: "add swell direction to the swell rows"): each
# frame's "swelldir" pack, in the waves' format (the swell's height, period, direction).
DIRECTION = "VMDR_SW1"
ZOOMS = range(0, 5)
CREDIT = "E.U. Copernicus Marine Service Information (global wave forecast)"


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    import copernicusmarine
    started = time.time()
    now = datetime.now(timezone.utc)
    run_time = now.replace(hour=now.hour // 3 * 3, minute=0, second=0, microsecond=0)
    ds = copernicusmarine.open_dataset(dataset_id=DATASET, variables=list(VARIABLES + TOTAL + (DIRECTION,)), service="arco-geo-series")
    stamps = ds["time"].values.astype("datetime64[s]").astype(np.int64)
    hours = [i for i, t in enumerate(stamps) if t >= int(run_time.timestamp())][:limit]
    if not hours:
        raise SystemExit("no hours from now in the global wave forecast")
    lats, lons = ds["latitude"].values.astype(np.float64), ds["longitude"].values.astype(np.float64)
    place, sample = regular_grid(lats[0], lons[0], lats[1] - lats[0], lons[1] - lons[0], len(lats), len(lons), True)
    placement = tiler.Placement(tiler.tiles_in_box((-85, 85, -180, 180), ZOOMS), place)
    # Each set in its own folder when written locally (both write an index.json).
    out = tiler.Run("swell-world", run_time, ZOOMS, "Copernicus global waves 9 km", CREDIT, True, local and f"{local}/swell-world")
    total = tiler.Run("waves-world", run_time, ZOOMS, "Copernicus global waves 9 km", CREDIT, True, local and f"{local}/waves-world")
    print(f"swell-world {run_time:%Y%m%dT%HZ}: {len(hours)} hours to "
          f"{datetime.fromtimestamp(int(stamps[hours[-1]]), timezone.utc):%d.%m %H} UTC", flush=True)
    for i in hours:
        t = int(stamps[i])
        began = time.time()
        hour = ds.isel(time=i).load()
        swell, period, chop = (hour[v].values.astype(np.float32) for v in VARIABLES)
        missing = ~np.isfinite(chop)  # the sea: where the model has wind waves (the swell is blank where there is none)
        height, peak, direction = (hour[v].values.astype(np.float32) for v in TOTAL)
        gone = ~np.isfinite(height)
        if total.checked("Copernicus", t, quality.waves(height, np.where(gone, np.nan, peak))):
            total.put(t, encode_hour(placement, sample, *(filled(v, gone) for v in (height, peak, direction))))
        if not out.checked("Copernicus", t, quality.waves(np.where(missing, np.nan, swell), np.where(missing, np.nan, period))):
            continue
        # No swell over the sea: 0 m (as EWAM's), not unknown.
        swell = np.where(~missing & ~np.isfinite(swell), 0, swell)
        out.put(t, swell_hour(placement, sample, *(filled(v, missing) for v in (swell, period, chop))))
        heading = hour[DIRECTION].values.astype(np.float32)
        heading = np.where(~missing & ~np.isfinite(heading), 0, heading)
        out.put(t, encode_hour(placement, sample, *(filled(v, missing) for v in (swell, period, heading))), kind="swelldir")
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%d.%m %H} UTC, {time.time() - began:.0f} s, "
              f"{out.bytes / 1e6:.0f} MB", flush=True)
    for run in (out, total):
        if run.frames:
            run.finish()
    print(f"swell-world and waves-world: {len(out.frames)} and {len(total.frames)} hours in {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
