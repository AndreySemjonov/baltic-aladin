"""Backtest: MTG's Cloud Type RGB for the low deck EUMETSAT's MSG cloud mask misses in low sun (the owner, 06.11.2026,
"Both", after the scorecard's 05.11 flag: Palanga BKN012-016 drawn clear while the sun was under ~15 deg, the mask clear
for 15 km round; MTG's Cloud Type showed it at 12:15, not at 08:15).

Each daylight frame time (:15, :45, the sun over 0 deg at Riga) of the days given: the mask (msg_fes:clm, at the time)
and the Cloud Type RGB (mtg_fd:rgb_cloudtype: red 1.38 um, green 0.64 um, blue 1.6 um; at the 10 minutes before) of the
Baltic box, cached in lab-cache/; then each airport's METAR (clouds_score.sky: BKN/OVC/VV cloudy, CAVOK/FEW clear) with
the mask's cloud and the Cloud Type's colours over ~6 km round it and the sun's height there, into lab-lowsun.csv.

    python lab_lowsun_clouds.py 2026-10-22 2026-11-05     # fetch (slow: EUMETSAT) and write the table
"""
import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import clouds as C
from clouds_score import STATIONS, metars, sky

C.LISTED = {}  # every time asked plainly, never the newest-older answer for an unlisted time
BOX = (53.5, 62.5, 17.5, 29.5)  # south, north, west, east
SIZE = (384, 384)
CACHE = Path(__file__).parent / "lab-cache"
CLOUDTYPE = "mtg_fd:rgb_cloudtype"


def pixel(lat, lon):
    x0, y0 = C.mercator(BOX[0], BOX[2])
    x1, y1 = C.mercator(BOX[1], BOX[3])
    x, y = C.mercator(lat, lon)
    return int((x - x0) / (x1 - x0) * SIZE[0]), int((y1 - y) / (y1 - y0) * SIZE[1])


def pictures(when):
    """(mask, cloudtype) RGB arrays for a frame time, cached; None where EUMETSAT had none."""
    path = CACHE / f"{when:%Y%m%d%H%M}.npz"
    if path.exists():
        d = np.load(path)
        return tuple(d[k] if d[k].size else None for k in ("mask", "cloudtype"))
    ten = when - timedelta(minutes=when.minute % 10)
    mask = C.picture(C.MASK, when, BOX, SIZE)
    cloudtype = C.picture(CLOUDTYPE, ten, BOX, SIZE)
    CACHE.mkdir(exist_ok=True)
    np.savez_compressed(path, mask=mask if mask is not None else np.zeros(0), cloudtype=cloudtype if cloudtype is not None else np.zeros(0))
    return mask, cloudtype


def main():
    first, last = (datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=timezone.utc) for a in sys.argv[1:3])
    times = []
    t = first
    while t < last + timedelta(days=1):
        for minute in (15, 45):
            when = t.replace(minute=minute)
            if float(C.sun_height(56.95, 24.1, when)) > 0:
                times.append(when)
        t += timedelta(hours=1)
    reports = metars(first, last + timedelta(days=2))
    with ThreadPoolExecutor(4) as pool:
        got = dict(zip(times, pool.map(pictures, times)))
    rows = []
    for when, (mask, cloudtype) in sorted(got.items()):
        if mask is None or cloudtype is None:
            continue
        for st in STATIONS:
            r = next((reports[(st, when + timedelta(minutes=k))] for k in (5, 0, 10) if (st, when + timedelta(minutes=k)) in reports), None)
            if not r or not sky(r[2]):
                continue
            x, y = pixel(r[0], r[1])
            if not (1 <= x < SIZE[0] - 1 and 1 <= y < SIZE[1] - 1):
                continue
            m = mask[y - 1:y + 2, x - 1:x + 2, :3].min(axis=2) > 200  # white: cloud
            ct = np.median(cloudtype[y - 1:y + 2, x - 1:x + 2, :3].reshape(-1, 3), axis=0)
            rows.append([f"{when:%Y-%m-%d %H:%M}", st, sky(r[2]), round(float(C.sun_height(r[0], r[1], when)), 1),
                         round(float(m.mean()), 2), *[int(v) for v in ct], r[2][:60]])
    with open(Path(__file__).parent / "lab-lowsun.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "station", "sky", "sun", "mask", "red", "green", "blue", "metar"])
        w.writerows(rows)
    print(f"{len(times)} frame times, {sum(1 for v in got.values() if v[0] is not None and v[1] is not None)} with both, {len(rows)} reports")


if __name__ == "__main__":
    main()
