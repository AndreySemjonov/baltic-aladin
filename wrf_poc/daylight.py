"""The 1 km box's hours from the riding light (the owner, 17-18.10.2026: the nights don't need it,
and the 1 km box is about 88% of the computing): from the first sunrise in the run to its last
sunset at the region's spot (POINT_LAT, POINT_LON), widened to the met_em files' 3-hour steps.
A day already over when the run is ready (READY_HOURS from now, default 4: the Tower's 48 hours)
is left to the 3 km box. WRF starts and ends a box once per run, so a night between two days stays in.
Prints D3_FROM=<h> D3_TO=<h> (hours from the run's start) for local.sh.

    python wrf_poc/daylight.py <work>
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone

STEP = 3


def sun(day, lat, lon):
    """Sunrise and sunset (UTC) on a day, NOAA's short formula; None in polar day or night."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + 0.5)
    eq = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                   - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
           + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    p = math.radians(lat)
    c = (math.cos(math.radians(90.833)) / (math.cos(p) * math.cos(dec))) - math.tan(p) * math.tan(dec)
    if abs(c) > 1:
        return None
    ha = math.degrees(math.acos(c))
    noon = 720 - 4 * lon - eq
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return midnight + timedelta(minutes=noon - 4 * ha), midnight + timedelta(minutes=noon + 4 * ha)


def main():
    env = dict(line.split("=", 1) for line in open(os.path.join(sys.argv[1], "env")).read().split() if "=" in line)
    start = datetime.strptime(env["START"], "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc)
    hours = int(env["HOURS"])
    end = start + timedelta(hours=hours)
    lat, lon = float(os.environ["POINT_LAT"]), float(os.environ["POINT_LON"])
    ready = datetime.now(timezone.utc) + timedelta(hours=float(os.environ.get("READY_HOURS", "4")))
    light = []
    day = start - timedelta(days=1)
    while day <= end:
        times = sun(day, lat, lon)
        if times and times[1] > max(start, ready) and times[0] < end:
            light.append(times)
        day += timedelta(days=1)
    if not light:
        print(f"D3_FROM=0 D3_TO={hours}")
        return
    first = max((light[0][0] - start).total_seconds() / 3600, 0)
    last = min((light[-1][1] - start).total_seconds() / 3600, hours)
    first = int(first // STEP * STEP)
    last = min(int(-(-last // STEP) * STEP), hours)
    print(f"D3_FROM={first} D3_TO={last}")


if __name__ == "__main__":
    main()
