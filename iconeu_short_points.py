"""ICON-EU's 03, 09, 15 and 21 UTC runs at the spots (the owner, 19.10.2026: fresher ICON-EU four times
a day, as ECMWF's 06/18 UTC ones), over the main run's later hours: DWD runs ICON-EU every 3 hours,
the 00/06/12/18 UTC runs to 120 hours (iconeu_tiles.py: the map and the spots), these to 51 hours
(their weather fields not every hour after 30). Only the spots' own cells (spot_cells.py, the same
points files as the main runs': points/icon-eu, points/icon-eu-wx), each run once, its hours laid
over the file's and the file's after its last kept (publish keep_after). An older run never goes
over a newer one (spot_cells.publish).

    python iconeu_short_points.py [--force] [--local DIR]
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

import iconeu_map
import spot_cells
import spot_points

LAST = 51
WIND = ("U_10M", "V_10M", "VMAX_10M")
WEATHER = ("T_2M", "TOT_PREC", "CLCT", "CLCL", "CLCM", "CLCH")


def newest_short_run():
    """The newest 03/09/15/21 UTC run with its last wind hour online, or None."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 3 * 3, minute=0, second=0, microsecond=0)
    for back in range(8):
        run = start - timedelta(hours=3 * back)
        if run.hour % 6 == 0:
            continue
        name = run.strftime("%Y%m%d%H")
        if iconeu_map.exists(iconeu_map.url(name, LAST, "V_10M")):
            return run
    return None


def fetch(run, step, variable):
    try:
        return iconeu_map.download(run, step, variable)
    except Exception:
        return None


def main():
    args = sys.argv[1:]
    force = "--force" in args
    local = args[args.index("--local") + 1] if "--local" in args else None
    run = newest_short_run()
    if run is None:
        print("ICON-EU: no 03/09/15/21 UTC run complete online")
        return
    name = run.strftime("%Y%m%d%H")
    run_name = run.strftime("%Y%m%dT%HZ")
    before = spot_points.published("icon-eu")
    published = spot_cells.run_time(before.get("run")) if before else None
    if not force and not local and published is not None and published >= run:
        print(f"ICON-EU {run_name}: the spots' file has this run or a newer one ({before.get('run')})")
        return
    cells = spot_cells.SpotCells("icon-eu", "icon_eu", weather="icon-eu-wx")
    start, started = int(run.timestamp()), time.time()
    totals = {}
    for k in range(0, LAST + 1, 6):
        chunk = list(range(k, min(k + 6, LAST + 1)))
        jobs = [(step, variable) for step in chunk for variable in WIND + WEATHER]
        with ThreadPoolExecutor(8) as pool:
            raw = dict(zip(jobs, pool.map(lambda job: fetch(name, *job), jobs)))
        for step in chunk:
            fields, grid = {}, None
            # Decoded one at a time (ecCodes isn't thread safe).
            for variable in WIND + WEATHER:
                data = raw.pop((step, variable), None)
                if data is not None:
                    grid, fields[variable] = iconeu_map.decode(data)
            u, v = fields.get("U_10M"), fields.get("V_10M")
            if grid is None or u is None or v is None:
                continue
            gust = fields.get("VMAX_10M", np.hypot(u, v))
            t = start + step * 3600
            if "TOT_PREC" in fields:
                totals[step] = fields["TOT_PREC"]
            previous = max((s for s in totals if s < step), default=None)
            if step in totals and previous is not None and {"T_2M", "CLCT"} <= fields.keys():
                rain = np.maximum(totals[step] - totals[previous], 0) / (step - previous)
                layers = {key: fields[source] for key, source in (("low", "CLCL"), ("mid", "CLCM"), ("high", "CLCH")) if source in fields}
                cells.add_regular(t, grid, {"10u": u, "10v": v, "gust": gust, "2t": fields["T_2M"], "tcc": fields["CLCT"], **layers}, rain)
            else:
                cells.add_regular(t, grid, {"10u": u, "10v": v, "gust": gust})
            for s in [s for s in totals if previous is not None and s < previous]:
                del totals[s]
        print(f"  +{chunk[-1]} h, {time.time() - started:.0f} s", flush=True)
    cells.publish(run_name, local and local + "/points", keep_after=True)
    print(f"ICON-EU {run_name}: done in {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up (their exit can crash Python after the work is published).
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
