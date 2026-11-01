"""ECMWF's open data as a WRF run's input in place of GFS (the owner, 1.11.2026: "Let's run test on tower and compare
with gfs wrf"; the station check showed WRF's local detail right but its day before worse than its same day, and the
large-scale push is what the boundary gives): for a run starting at EC_START (the GFS run's start, 06 or 18 UTC;
tower.sh passes it), ECMWF's 00/12 UTC run 6 hours before it (out about 8 hours after its start, so in hand when the
GFS run begins: the two compared as a rider would have them), its 0.25° hours every 3 h, only WPS's fields (the
index, then byte ranges), cut to the region's box, a few reshaped as Vtable.ECMWF_OD reads them: the soil as depth
layers (ECMWF's levels 1-4 are 0-7, 7-28, 28-100 and 100-289 cm), the ground's height from its geopotential, the 2 m
humidity from its dew point, the land a 0/1 flag. Into <work>/ecmwf; <work>/env as gfs.py's, with SOURCE=ecmwf.

    EC_START=2026110118 HOURS=48 REGION=tarifa-ec python wrf_poc/ecmwf.py work
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes as ec
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sources  # noqa: E402

AGENT = {"User-Agent": "MeteoGust WRF test (data.meteogust.com)"}
BOX = (float(os.environ.get("GFS_BOTTOM", "30")), float(os.environ.get("GFS_TOP", "42")),
       float(os.environ.get("GFS_LEFT", "-14")), float(os.environ.get("GFS_RIGHT", "3")))
WANTED = {("pl", p) for p in ("gh", "t", "u", "v", "r")} | {("sfc", p) for p in ("10u", "10v", "2t", "2d", "sp", "msl", "skt", "lsm", "z")} \
    | {("sol", p) for p in ("sot", "vsw")}
SOIL_CM = {1: (0, 7), 2: (7, 28), 3: (28, 100), 4: (100, 289)}


def get(url, headers=None):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={**AGENT, **(headers or {})}), timeout=180) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if attempt == 4:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == 4:
                raise
        time.sleep(15 * (attempt + 1))


def step_url(base, run, step, suffix):
    return f"{base}/{run:%Y%m%d}/{run:%H}z/ifs/0p25/oper/{run:%Y%m%d%H}0000-{step}h-oper-fc.{suffix}"


def pick_run(start, hours):
    """ECMWF's run 6 h before the start whose last needed hour is out (its index), else the one 12 h before that."""
    for run in (start - timedelta(hours=6), start - timedelta(hours=18)):
        last = int((start - run).total_seconds() // 3600) + hours
        for base in sources.ECMWF:
            if get(step_url(base, run, last, "index")) is not None:
                return run, base
    raise SystemExit(f"No ECMWF run out for a start at {start:%Y-%m-%d %H}Z")


def fetch_step(base, run, step, wanted=WANTED):
    """The step's wanted messages as GRIB2 bytes (byte ranges from its index, neighbours read together)."""
    index = get(step_url(base, run, step, "index"))
    if index is None:
        raise SystemExit(f"ECMWF {run:%d %H}Z +{step} h: no index")
    parts = []
    for line in index.decode().splitlines():
        entry = json.loads(line)
        if (entry.get("levtype"), entry.get("param")) in wanted:
            parts.append((entry["_offset"], entry["_offset"] + entry["_length"]))
    parts.sort()
    ranges = []
    for a, b in parts:
        if ranges and a <= ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], b)
        else:
            ranges.append([a, b])
    url = step_url(base, run, step, "grib2")
    return b"".join(get(url, {"Range": f"bytes={a}-{b - 1}"}) for a, b in ranges), len(parts)


def crop(handle):
    """The message cut to the box (ECMWF's grid: rows from 90 N south, columns from 180 W east, 0.25°)."""
    ni, nj = ec.codes_get(handle, "Ni"), ec.codes_get(handle, "Nj")
    lat0, lon0 = ec.codes_get(handle, "latitudeOfFirstGridPointInDegrees"), ec.codes_get(handle, "longitudeOfFirstGridPointInDegrees")
    di, dj = ec.codes_get(handle, "iDirectionIncrementInDegrees"), ec.codes_get(handle, "jDirectionIncrementInDegrees")
    lats = lat0 - dj * np.arange(nj)
    lons = (lon0 + di * np.arange(ni) + 180) % 360 - 180
    rows = np.where((lats >= BOX[0]) & (lats <= BOX[1]))[0]
    cols = np.where((lons >= BOX[2]) & (lons <= BOX[3]))[0]
    assert np.all(np.diff(cols) == 1), "the box crosses the grid's edge"
    values = ec.codes_get_values(handle).reshape(nj, ni)[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    ec.codes_set(handle, "Ni", len(cols))
    ec.codes_set(handle, "Nj", len(rows))
    ec.codes_set(handle, "latitudeOfFirstGridPointInDegrees", float(lats[rows[0]]))
    ec.codes_set(handle, "latitudeOfLastGridPointInDegrees", float(lats[rows[-1]]))
    ec.codes_set(handle, "longitudeOfFirstGridPointInDegrees", float(lons[cols[0]]) % 360)
    ec.codes_set(handle, "longitudeOfLastGridPointInDegrees", float(lons[cols[-1]]) % 360)
    return values


def reshape(raw, out, step):
    """Each wanted message cut and, where Vtable.ECMWF_OD needs it, reshaped; written to `out`. Returns the count."""
    handles, values = [], {}
    for message in split(raw):
        handle = ec.codes_new_from_message(message)
        handles.append((handle, crop(handle)))
    by_name = {ec.codes_get(h, "shortName"): v for h, v in handles}
    written = 0
    with open(out, "wb") as f:
        for handle, v in handles:
            name = ec.codes_get(handle, "shortName")
            if name in ("sot", "vsw"):
                top, bottom = SOIL_CM[int(ec.codes_get(handle, "level"))]
                for key, value in (("typeOfFirstFixedSurface", 106), ("scaleFactorOfFirstFixedSurface", 2),
                                   ("scaledValueOfFirstFixedSurface", top), ("typeOfSecondFixedSurface", 106),
                                   ("scaleFactorOfSecondFixedSurface", 2), ("scaledValueOfSecondFixedSurface", bottom)):
                    ec.codes_set(handle, key, value)
            elif name == "z":
                # The ground's height (m) as GFS gives it: geopotential height at the surface. ECMWF gives it at step 0
                # only: that one, dated to this step.
                ec.codes_set(handle, "step", step)
                ec.codes_set(handle, "paramId", 156)
                v = v / 9.80665
            elif name == "lsm":
                v = (v >= 0.5).astype(float)
            elif name == "r":
                # Over ice ECMWF's humidity reaches 110 %; WPS takes it as given.
                v = np.minimum(v, 100)
            elif name == "2d":
                # The 2 m humidity from the dew point and the air (Magnus, as WPS computes it), in its place.
                t, d = by_name["2t"] - 273.15, v - 273.15
                v = np.clip(100 * np.exp(17.625 * d / (243.04 + d) - 17.625 * t / (243.04 + t)), 0, 100)
                ec.codes_set(handle, "paramId", 260242)
            # Plain packing: ECMWF's CCSDS one isn't read by WPS's GRIB2 library (the Tower's ungrib, 1.11.2026: "ERROR
            # extracting field gf_getfld = 12" on most fields, then "The mandatory field TT was not found").
            ec.codes_set(handle, "packingType", "grid_simple")
            ec.codes_set_values(handle, np.ascontiguousarray(v, dtype=float).ravel())
            f.write(ec.codes_get_message(handle))
            ec.codes_release(handle)
            written += 1
    return written


def split(raw):
    """The GRIB messages in a byte string (each starts "GRIB", its length in the indicator section)."""
    at = 0
    while at < len(raw):
        at = raw.find(b"GRIB", at)
        if at < 0:
            return
        length = int.from_bytes(raw[at + 8:at + 16], "big")
        yield raw[at:at + length]
        at += length


def main():
    work = Path(sys.argv[1] if len(sys.argv) > 1 else "work")
    hours = int(os.environ.get("HOURS", "48"))
    given = os.environ.get("EC_START")
    if given:
        start = datetime.strptime(given, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    else:
        back = datetime.now(timezone.utc) - timedelta(hours=3)
        start = back.replace(hour=back.hour // 6 * 6, minute=0, second=0, microsecond=0)
    run, base = pick_run(start, hours)
    lead = int((start - run).total_seconds() // 3600)
    folder = work / "ecmwf"
    folder.mkdir(parents=True, exist_ok=True)
    began, fetched = time.time(), 0
    ground, _ = fetch_step(base, run, 0, {("sfc", "z")})
    for step in range(lead, lead + hours + 1, 3):
        raw, count = fetch_step(base, run, step, WANTED - {("sfc", "z")})
        fetched += len(raw)
        written = reshape(raw + ground, folder / f"ec.{step:03d}.grib2", step)
        print(f"ECMWF {run:%d %H}Z +{step} h: {count} fields, {len(raw) / 1e6:.0f} MB read, {written} written", flush=True)
    end = start + timedelta(hours=hours)
    stamp = "%Y-%m-%d_%H:00:00"
    (work / "env").write_text(f"REGION={os.environ.get('REGION', 'tarifa-ec')}\nSTART={start.strftime(stamp)}\nEND={end.strftime(stamp)}\n"
                              f"HOURS={hours}\nCYCLE={start.strftime('%Y-%m-%dT%HZ')}\nSOURCE=ecmwf\nEC_RUN={run.strftime('%Y-%m-%dT%HZ')}\n")
    print(f"{time.strftime('%H:%M:%S', time.gmtime())} ECMWF {run:%Y-%m-%d %H}Z from +{lead} h in hand: {fetched / 1e6:.0f} MB "
          f"in {time.time() - began:.0f} s, the run from {start:%Y-%m-%d %H}Z, {hours} hours", flush=True)


if __name__ == "__main__":
    main()
