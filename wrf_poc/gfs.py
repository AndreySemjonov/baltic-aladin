"""The WRF test's input (wrf-poc.yml): the newest GFS 0.25° run that is out (newest_cycle), its first
HOURS hours every 3 hours, cut to the region round Tarifa by NOAA's filter (all variables and levels:
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
PROD = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"


def is_out(cycle, hours):
    """The cycle's last needed hour published (its .idx on NOMADS: written after the file)."""
    day, hour = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    url = f"{PROD}gfs.{day}/{hour}/atmos/gfs.t{hour}z.pgrb2.0p25.f{hours:03d}.idx"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT, method="HEAD"), timeout=30) as reply:
            return reply.status == 200
    except Exception:
        return False


def newest_cycle(hours, now=None):
    """The newest GFS cycle whose first `hours` are out (the owner, 19.10.2026: fresher runs; before,
    always the cycle 5 hours back or more, so the Tower's 04:15 and 16:15 UTC runs took the 18Z and 06Z
    when the 00Z and 12Z were out: Windguru's run that day was 6 hours fresher). GFS 0.25° is out about
    3.5-4 hours after its cycle: the newest cycle 3 hours back, waited for up to GFS_WAIT minutes
    (default 40) when it is due soon, else the one before."""
    now = now or datetime.now(timezone.utc)
    back = now - timedelta(hours=3)
    newest = back.replace(hour=back.hour // 6 * 6, minute=0, second=0, microsecond=0)
    deadline = time.time() + 60 * float(os.environ.get("GFS_WAIT", "40"))
    while True:
        if is_out(newest, hours):
            return newest
        # Not due within the wait (more than 5 hours old and still missing): the one before.
        if time.time() > deadline or datetime.now(timezone.utc) - newest > timedelta(hours=5):
            print(f"GFS {newest:%d %H}Z not out: the cycle before", flush=True)
            return newest - timedelta(hours=6)
        print(f"GFS {newest:%d %H}Z not out yet: waiting", flush=True)
        time.sleep(300)


def main():
    work = Path(sys.argv[1] if len(sys.argv) > 1 else "work")
    hours = int(os.environ.get("HOURS", "12"))
    folder = work / "gfs"
    folder.mkdir(parents=True, exist_ok=True)
    cycle = newest_cycle(hours)
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
