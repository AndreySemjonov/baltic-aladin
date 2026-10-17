"""The WRF test's input (wrf-poc.yml): the newest GFS 0.25° run that is surely out, its first HOURS
hours every 3 hours, cut to the region round Tarifa by NOAA's filter (all variables and levels:
WPS's ungrib reads what it needs), into <work>/gfs; the run's start and end for the namelists into
<work>/env.

    python gfs.py work
"""
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The region's box for NOAA's filter (the region file, sourced into the environment by the workflow).
REGION = {"toplat": os.environ.get("GFS_TOP", "42"), "bottomlat": os.environ.get("GFS_BOTTOM", "30"),
          "leftlon": os.environ.get("GFS_LEFT", "-14"), "rightlon": os.environ.get("GFS_RIGHT", "3")}
FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
AGENT = {"User-Agent": "MeteoGust WRF test (data.meteogust.com)"}


def main():
    work = Path(sys.argv[1] if len(sys.argv) > 1 else "work")
    hours = int(os.environ.get("HOURS", "12"))
    folder = work / "gfs"
    folder.mkdir(parents=True, exist_ok=True)
    # GFS is out about 3.5-4 hours after its cycle: the cycle 5 hours back or more.
    now = datetime.now(timezone.utc) - timedelta(hours=5)
    cycle = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    day, hour = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    for step in range(0, hours + 1, 3):
        name = f"gfs.t{hour}z.pgrb2.0p25.f{step:03d}"
        query = (f"?dir=%2Fgfs.{day}%2F{hour}%2Fatmos&file={name}&all_var=on&all_lev=on&subregion="
                 + "".join(f"&{k}={v}" for k, v in REGION.items()))
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(FILTER + query, headers=AGENT), timeout=180) as reply:
                    data = reply.read()
                if len(data) < 100_000:
                    raise ValueError(f"only {len(data)} bytes")
                (folder / name).write_bytes(data)
                print(f"{name}: {len(data) / 1e6:.1f} MB", flush=True)
                break
            except Exception as error:
                print(f"{name}: {error}, again", flush=True)
                time.sleep(20 * (attempt + 1))
        else:
            raise SystemExit(f"{name} could not be read")
    end = cycle + timedelta(hours=hours)
    stamp = "%Y-%m-%d_%H:00:00"
    (work / "env").write_text(f"REGION={os.environ.get('REGION', 'tarifa')}\nSTART={cycle.strftime(stamp)}\nEND={end.strftime(stamp)}\nHOURS={hours}\n"
                              f"CYCLE={cycle.strftime('%Y-%m-%dT%HZ')}\n")
    print(f"GFS {cycle:%Y-%m-%d %H}Z, {hours} hours", flush=True)


if __name__ == "__main__":
    main()
