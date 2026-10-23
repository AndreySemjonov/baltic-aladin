"""World weather tiles from GFS (NOAA, public domain), ECMWF open data (CC BY 4.0), ICON (DWD's
open data; its triangles put on a regular 0.125° grid) and GDPS (Environment and Climate Change
Canada's 15 km, Open Government Licence - Canada): the world mix's models (mix_world.py; the owner,
18.10.2026: the mix all the way to 14 days, everywhere).

Plan step 1 for the world (the owner, 12.10.2026): each hour of a run becomes one pack of
weather tiles on the web map's grid (Web Mercator, 256-pixel tiles, zooms 0-3: the models'
0.25° grid is about 28 km, zoom 3 about 20 km a pixel at the equator), the tiles holding
values, not colours (red and green the wind's east and north parts m/s x 4 + 128, blue the
gust m/s x 5; the land once per run in land.pack), so the app colours them on the phone as the Baltic maps. A
pack is "MGT1", the header's length (4 bytes, little-endian), the header (JSON: each tile's
offset from the header's end and its length) and the tiles (PNG); the app reads the header and then only the tiles it shows.
The packs and tiles/<model>/index.json (the run's hours) go to the file store through our
service (station-history /tiles/upload, the INGEST_TOKEN secret), the index last.

    python world_tiles.py gfs|ecmwf|icon|gdps              build and upload the newest run, if new
    python world_tiles.py gfs|ecmwf|icon|gdps --force      even if it's the published one
    python world_tiles.py gfs|ecmwf|icon|gdps --local DIR  write to DIR, no upload (a test; --steps 0,24)
"""
import bz2
import io
import json
import math
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes as ec
import numpy as np
from PIL import Image

import ecmwf_map
import gfs_map
import maplib
import omremote
import sources
import temp_map

ZOOMS = range(0, 4)
# Pixels a tile side at each zoom: 256, except zoom 3 at the models' own detail (1440 columns
# round the world, 0.25°): more pixels there only made the packs bigger (5 MB an hour).
SIZES = {0: 256, 1: 256, 2: 256, 3: 180}
# ICON's 0.125° and GDPS's 0.15°: zoom 3 at their own detail too (2880 and 2400 columns).
MODEL_SIZES = {"icon": {0: 256, 1: 256, 2: 256, 3: 360}, "gdps": {0: 256, 1: 256, 2: 256, 3: 300},
               # ECMWF at 9 km (0.1°: 3600 columns round the world, zoom 4 at 225 pixels) since 18.10.2026;
               # zooms 0-3 at the usual 256 for the map's wide views.
               "ecmwf": {0: 256, 1: 256, 2: 256, 3: 256, 4: 225}}
# ECMWF IFS at its full 9 km (the owner, 18.10.2026: ECMWF's own free files are 0.25°, about 25 km):
# Open-Meteo's open data on AWS (CC BY 4.0; not its API), one file an hour with every variable on
# ECMWF's O1280 grid; the 00 and 12 UTC runs (to 15 days). ECMWF's own 9 km files when it fails (from
# 21.10.2026; 3-hourly), then its 0.25° ones.
ECMWF9 = "https://openmeteo.s3.amazonaws.com/"
ECMWF9_RUNS = "data_spatial/ecmwf_ifs/"
# Its variables: our names, theirs, how to make them ours (°C to K as ECMWF's files).
ECMWF9_FIELDS = (("10u", "wind_u_component_10m", 0), ("10v", "wind_v_component_10m", 0), ("gust", "wind_gusts_10m", 0),
                 ("2t", "temperature_2m", 273.15), ("2d", "dew_point_2m", 273.15), ("msl", "pressure_msl", 0),
                 ("rain", "precipitation", 0), ("tcc", "cloud_cover", 0), ("low", "cloud_cover_low", 0),
                 ("mid", "cloud_cover_mid", 0), ("high", "cloud_cover_high", 0),
                 # The Weather tab's (the owner, 18.10.2026: off Open-Meteo): sky_pack.
                 ("vis", "visibility", 0), ("showers", "showers", 0), ("snow", "snowfall_water_equivalent", 0), ("cape", "cape", 0))
ECMWF9_GRID = {"Ni": 3600, "Nj": 1801, "latitudeOfFirstGridPointInDegrees": 90.0, "longitudeOfFirstGridPointInDegrees": 0.0,
               "iDirectionIncrementInDegrees": 0.1, "jDirectionIncrementInDegrees": 0.1, "jScansPositively": 0}
UPLOAD = "https://data.meteogust.com/tiles/upload/"
PUBLISHED = "https://files.meteogust.com/tiles/{model}/index.json"

# Every hour each model gives (the owner, 18.10.2026: "full resolution of all models"): GFS hourly
# to 5 days, then 3-hourly to 16.
GFS_STEPS = list(range(0, 121)) + list(range(123, 385, 3))
GFS_FILTER = ("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?dir=%2Fgfs.{day}%2F{hour}%2Fatmos"
              "&file=gfs.t{hour}z.pgrb2.0p25.f{step:03d}&var_UGRD=on&var_VGRD=on&var_GUST=on&var_LAND=on"
              "&var_TMP=on&var_DPT=on&var_PRATE=on&var_TCDC=on&var_PRMSL=on"
              "&lev_10_m_above_ground=on&lev_surface=on&lev_2_m_above_ground=on&lev_entire_atmosphere=on"
              "&lev_mean_sea_level=on")

MODELS = {
    "gfs": {"name": "GFS 0.25°", "credit": "NOAA NCEP, GFS (public domain)", "gust": "surface gust"},
    "ecmwf": {"name": "ECMWF 9 km", "credit": "ECMWF IFS (CC BY 4.0), via Open-Meteo's open data on AWS (CC BY 4.0)",
              "gust": "maximum in the hour before"},
    "icon": {"name": "ICON 13 km", "credit": "ICON, Deutscher Wetterdienst", "gust": "maximum since the previous output hour"},
    "gdps": {"name": "GDPS 15 km", "credit": "GDPS, Environment and Climate Change Canada (Open Government Licence - Canada)",
             "gust": "maximum in the hour before"},
}
# ICON hourly to 78 hours, then 3-hourly to 180 (as DWD gives it); GDPS hourly to 3.5 days, then
# 3-hourly to 10 days (the owner, 18.10.2026: it has the mix's smallest share; hourly all the way was
# about 8 GB more to keep).
ICON_STEPS = list(range(0, 79)) + list(range(81, 181, 3))
GDPS_STEPS = list(range(0, 85)) + list(range(87, 241, 3))
ICON_URL = "https://opendata.dwd.de/weather/nwp/icon/grib/{hour}/{folder}/icon_global_icosahedral_{kind}_{run}{step}_{name}.grib2.bz2"
GDPS_URL = ("{host}/{day}/WXO-DD/model_gdps/15km/{hour}/{step:03d}/"
            "{day}T{hour}Z_MSC_GDPS_{name}_LatLon0.15_PT{step:03d}H.grib2")
# Canada's high-performance mirror first (its main datamart timed out from GitHub's runners,
# 18.10.2026), the datamart itself after.
GDPS_HOSTS = ["https://hpfx.collab.science.gc.ca", "https://dd.weather.gc.ca"]
gdps_host = [GDPS_HOSTS[0]]
# ICON's triangles on a regular grid: rows from 90° N down, columns from 0° E.
ICON_GRID = {"Ni": 2880, "Nj": 1441, "latitudeOfFirstGridPointInDegrees": 90.0, "longitudeOfFirstGridPointInDegrees": 0.0,
             "iDirectionIncrementInDegrees": 0.125, "jDirectionIncrementInDegrees": 0.125, "jScansPositively": 0}


# MARK: The models' fields

def gfs_steps(run, steps):
    """(step, {"10u", "10v", "gust", "lsm"}: values, grid) for each step, downloaded ahead."""
    def fetch(step):
        url = GFS_FILTER.format(day=run.strftime("%Y%m%d"), hour=run.strftime("%H"), step=step)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(gfs_map.request(url), timeout=180) as reply:
                    return step, reply.read()
            except (urllib.error.URLError, OSError):
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
    with ThreadPoolExecutor(max_workers=3) as pool:
        for step, data in pool.map(fetch, steps):
            fields, grid = gfs_map.decode(data)
            # The weather as the others name it (the spots' air, rain and clouds: the owner,
            # 18.10.2026, step 3): sea-level pressure, the rain rate an hour (kg/m²/s), the cloud.
            if "prmsl" in fields:
                fields["msl"] = fields.pop("prmsl")
            if "prate" in fields:
                fields["rate"] = np.maximum(fields.pop("prate"), 0) * 3600
            yield step, fields, grid


def s3_list(prefix):
    """The keys under a prefix of Open-Meteo's open data (its public listing)."""
    keys, token = [], None
    while True:
        url = ECMWF9 + "?list-type=2&prefix=" + prefix + ("&continuation-token=" + urllib.request.quote(token) if token else "")
        text = fetch_bytes(url).decode()
        keys += [k.split("</Key>")[0] for k in text.split("<Key>")[1:]]
        token = text.split("<NextContinuationToken>")[1].split("<")[0] if "<NextContinuationToken>" in text else None
        if not token:
            return keys


def ecmwf9_newest_run():
    """The newest run with all its hours there: the 00/12 UTC ones to 15 days (145 or more files), and since
    21.10.2026 the 06/18 UTC ones to 6 days (109; the owner: the map took one run in two)."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for back in range(5):
        run = start - timedelta(hours=6 * back)
        files = [k for k in s3_list(ECMWF9_RUNS + run.strftime("%Y/%m/%d/%H%MZ/")) if k.endswith(".om")]
        if len(files) >= (145 if run.hour % 12 == 0 else 109):
            return run
    raise SystemExit("No complete ECMWF 9 km run found")


# The hours past the first day remade at most every 3 hours (the owner, 21.10.2026, "M2", as the Mix's FAR_FROM and
# FAR_EVERY): an hourly set's new run keeps the published run's packs of those hours while they're under 3 hours old
# (each frame says when its pack was made: "made"); station-history keeps every folder an index points to. An hour
# later 94 % of the Mix's far tiles were unchanged; the near day stays hourly. 3- and 6-hourly sets: never kept.
FAR_FROM, FAR_EVERY = 24 * 3600, 3 * 3600


def far_frames(index):
    """{time: frame} of a published index's frames to keep as they are: past the first day from now, made under
    FAR_EVERY ago (10 minutes' margin, so hourly runs remake them every third run)."""
    now = time.time()
    out = {}
    for frame in (index or {}).get("frames") or []:
        t = frame.get("time")
        if isinstance(t, (int, float)) and t - now >= FAR_FROM and now - (frame.get("made") or 0) < FAR_EVERY - 600:
            out[int(t)] = dict(frame)
    return out


def published_index(model):
    """The set's published index (None when there's none)."""
    try:
        request = urllib.request.Request(PUBLISHED.format(model=model) + "?t=%d" % time.time(), headers={"User-Agent": maplib.USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as reply:
            return json.load(reply)
    except Exception:
        return None


def with_tail(frames, previous):
    """A short run's frames, then the published run's after its last hour (ECMWF's 06/18 UTC runs end at
    6 days, ICON-EU's 03/09/15/21 UTC at 30 hours: the long run's later hours stay on the map, as
    Open-Meteo's series keep them; station-history keeps every folder an index points to)."""
    if not frames or not previous or not previous.get("frames"):
        return frames
    last = max(f["time"] for f in frames)
    return frames + [f for f in sorted(previous["frames"], key=lambda f: f["time"]) if f["time"] > last]


def ecmwf9_steps(run, steps):
    """ECMWF's fields at each hour of the run, its O1280 grid put on ECMWF9_GRID (the four nearest
    points, by distance); the rain the step's, as a rate an hour."""
    import tempfile
    import omfiles
    from scipy.spatial import cKDTree
    # O1280: 2560 Gaussian latitudes, north first; row i has 20 + 4i points to the equator, then back.
    x, _ = np.polynomial.legendre.leggauss(2560)
    lats = np.degrees(np.arcsin(x))[::-1]
    counts = np.array([20 + 4 * i for i in range(1280)] + [20 + 4 * (2559 - i) for i in range(1280, 2560)])
    la = np.radians(np.repeat(lats, counts))
    lo = np.radians(np.concatenate([np.arange(n) * 360.0 / n for n in counts]))
    tree = cKDTree(np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=1))
    glat = np.radians(90 - np.arange(ECMWF9_GRID["Nj"]) * 0.1)
    glon = np.radians(np.arange(ECMWF9_GRID["Ni"]) * 0.1)
    gl, go = np.meshgrid(glat, glon, indexing="ij")
    points = np.stack([np.cos(gl) * np.cos(go), np.cos(gl) * np.sin(go), np.sin(gl)], axis=-1).reshape(-1, 3)
    distance, nearest = tree.query(points, k=4, workers=-1)
    share = (1 / np.maximum(distance, 1e-9)).astype(np.float32)
    share /= share.sum(axis=1, keepdims=True)
    shape = (ECMWF9_GRID["Nj"], ECMWF9_GRID["Ni"])
    # Its own points too, for the spots' values from their nearest point as Open-Meteo's (the owner,
    # 18.10.2026: our 0.1° grid's four-point blend read 0.2-0.6 m/s off theirs).
    native_points = {"lat": np.degrees(la), "lon": (np.degrees(lo) + 180) % 360 - 180}
    del tree, points, gl, go

    def regular(values):
        return (values[nearest] * share).sum(axis=1).reshape(shape)

    folder = ECMWF9_RUNS + run.strftime("%Y/%m/%d/%H%MZ/")
    files = {}
    for key in s3_list(folder):
        name = key.rsplit("/", 1)[-1]
        if name.endswith(".om"):
            valid = datetime.strptime(name[:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
            files[int((valid - run).total_seconds() // 3600)] = key
    wanted = [s for s in steps if s in files] if steps is not None else sorted(files)

    def fetch(step):
        # At home only the variables used, read in part (omremote.py; the owner, 21.10.2026: Oregon ~17 MB/s from home).
        if omremote.PARTIAL:
            return step, omremote.Prefetched(sources.om_url(files[step]), [t for _, t, _ in ECMWF9_FIELDS] + ["sea_ice_thickness"])
        handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
        handle.close()
        for attempt in range(4):
            try:
                request = urllib.request.Request(sources.om_url(files[step]), headers={"User-Agent": maplib.USER_AGENT})
                with urllib.request.urlopen(request, timeout=600) as reply, open(handle.name, "wb") as out:
                    while True:
                        block = reply.read(1 << 22)
                        if not block:
                            break
                        out.write(block)
                return step, handle.name
            except (urllib.error.URLError, OSError):
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))

    before = 0
    # The land (sky_pack's flag: a place's weather from its nearest land cell, as Open-Meteo's): where
    # the sea ice has no value (28.5% of the world, 18.10.2026), from the run's first file.
    land = None
    with ThreadPoolExecutor(max_workers=3) as pool:
        for step, path in omremote.bounded_map(fetch, wanted, 3, ahead=4):
            reader = omremote.reader(path)
            fields = {}
            if land is None:
                try:
                    ice = reader.get_child_by_name("sea_ice_thickness").read_array((slice(None), slice(None)))[0]
                    land = regular(np.isnan(ice).astype(np.float32))
                except Exception:
                    land = False
            if land is not False:
                fields["land"] = land
            native = {}
            for ours, theirs, add in ECMWF9_FIELDS:
                try:
                    values = reader.get_child_by_name(theirs).read_array((slice(None), slice(None)))[0]
                except Exception:
                    continue
                values = np.nan_to_num(values.astype(np.float32))
                fields[ours] = regular(values) + add
                if ours in ("10u", "10v", "gust", "2t", "rain", "tcc", "low", "mid", "high"):
                    native[ours] = values + add
            reader.close()
            omremote.done(path)
            # The rain over the step (an hour, then 3 and 6), as mm an hour.
            if "rain" in fields:
                fields["rain_step"] = fields.pop("rain")
                fields["rate"] = np.maximum(fields["rain_step"], 0) / max(step - before, 1)
            if "rain" in native:
                native["rate"] = np.maximum(native.pop("rain"), 0) / max(step - before, 1)
            fields["_native"] = dict(native_points, values=native)
            before = step
            yield step, fields, ECMWF9_GRID


def ecmwf_steps(run, steps, res="0p25"):
    """The same from ECMWF's open data (`res` "0p1": its 9 km files): 10 m wind, the gust and (at step
    0) the land-sea mask."""
    def fetch(step):
        # The wind, and for the world's temperature and rain maps the air (2 m, its dew point),
        # the sea-level pressure, the precipitation so far and the cloud cover.
        wanted = ("10u", "10v", "10fg", "2t", "2d", "msl", "tp", "tcc") + (("lsm",) if step == 0 else ())
        index = [json.loads(line) for line in ecmwf_map.get(ecmwf_map.file_url(run, step, "index", res)).decode().splitlines()
                 if line.strip()]
        messages = {}
        for entry in index:
            if entry.get("levtype") == "sfc" and entry.get("param") in wanted:
                start, length = entry["_offset"], entry["_length"]
                messages[entry["param"]] = ecmwf_map.get(ecmwf_map.file_url(run, step, "grib2", res), start, start + length - 1)
        return step, messages
    with ThreadPoolExecutor(max_workers=4) as pool:
        for step, messages in pool.map(fetch, steps):
            fields, grid = ecmwf_map.decode(messages)
            if "10fg" in fields:
                fields["gust"] = fields.pop("10fg")
            yield step, fields, grid


def fetch_bytes(url, tries=4):
    """A file, tried again on a passing failure; None when it isn't there (404)."""
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": maplib.USER_AGENT})
            with urllib.request.urlopen(request, timeout=180) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if attempt == tries - 1:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == tries - 1:
                raise
        time.sleep(10 * (attempt + 1))


def exists(url):
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": maplib.USER_AGENT})
        with urllib.request.urlopen(request, timeout=20):
            return True
    except (urllib.error.URLError, OSError):
        return False


def icon_url(run, folder, name, step=None):
    kind = "time-invariant" if step is None else "single-level"
    return ICON_URL.format(hour=run.strftime("%H"), folder=folder, kind=kind, run=run.strftime("%Y%m%d%H"),
                           step="" if step is None else "_%03d" % step, name=name)


def icon_newest_run():
    """The newest 00/12 UTC ICON run (to 180 hours) with its last step published."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 12 * 12, minute=0, second=0, microsecond=0)
    for back in range(3):
        run = start - timedelta(hours=12 * back)
        if exists(icon_url(run, "v_10m", "V_10M", ICON_STEPS[-1])):
            return run
    raise SystemExit("No complete ICON run found")


# ICON from Open-Meteo's open data (the owner, 18.10.2026: its spot values the same as Open-Meteo's
# ICON; DWD's own lat/lon files read 0.3-0.6 m/s off theirs, built from the triangles another way):
# its regular 0.125° grid (rows south to north from 90 S, columns from 180 W), every run (00 and 12
# to 180 hours, 06 and 18 to 120), one file an hour with every variable. DWD's own files when it fails.
ICON_OM_RUNS = "data_spatial/dwd_icon/"
ICON_OM_GRID = {"Ni": 2879, "Nj": 1441, "latitudeOfFirstGridPointInDegrees": -90.0, "longitudeOfFirstGridPointInDegrees": -180.0,
                "iDirectionIncrementInDegrees": 0.125, "jDirectionIncrementInDegrees": 0.125, "jScansPositively": 1}
ICON_OM_FIELDS = (("10u", "wind_u_component_10m", 0, 1), ("10v", "wind_v_component_10m", 0, 1), ("gust", "wind_gusts_10m", 0, 1),
                  ("2t", "temperature_2m", 273.15, 1), ("rh", "relative_humidity_2m", 0, 1), ("msl", "pressure_msl", 0, 100),
                  ("rain", "precipitation", 0, 1), ("tcc", "cloud_cover", 0, 1), ("low", "cloud_cover_low", 0, 1),
                  ("mid", "cloud_cover_mid", 0, 1), ("high", "cloud_cover_high", 0, 1))


def icon_om_newest_run():
    """The newest ICON run in Open-Meteo's open data with all its hours (00/12: 113 files, 06/18: 93)."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for back in range(5):
        run = start - timedelta(hours=6 * back)
        keys = [k for k in s3_list(ICON_OM_RUNS + run.strftime("%Y/%m/%d/%H%MZ/")) if k.endswith(".om") and "_" not in k.rsplit("/", 1)[-1]]
        if len(keys) >= (113 if run.hour % 12 == 0 else 93):
            return run, sorted(keys)
    raise SystemExit("No complete ICON run in the open data")


def icon_om_steps(run, keys):
    """ICON's fields at each hour of the run from the open data's files, on ICON_OM_GRID."""
    import tempfile
    import omfiles

    def fetch(key):
        # At home only the variables used, read in part (omremote.py; the owner, 21.10.2026: Oregon ~17 MB/s from home).
        if omremote.PARTIAL:
            return key, omremote.Prefetched(sources.om_url(key), [t for _, t, _, _ in ICON_OM_FIELDS])
        handle = tempfile.NamedTemporaryFile(suffix=".om", delete=False)
        handle.close()
        for attempt in range(4):
            try:
                request = urllib.request.Request(sources.om_url(key), headers={"User-Agent": maplib.USER_AGENT})
                with urllib.request.urlopen(request, timeout=600) as reply, open(handle.name, "wb") as out:
                    while True:
                        block = reply.read(1 << 22)
                        if not block:
                            break
                        out.write(block)
                return key, handle.name
            except (urllib.error.URLError, OSError):
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))

    before = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for key, path in omremote.bounded_map(fetch, keys, 6, ahead=8):
            valid = datetime.strptime(key.rsplit("/", 1)[-1][:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
            step = int((valid - run).total_seconds() // 3600)
            reader = omremote.reader(path)
            fields = {}
            for ours, theirs, add, scale in ICON_OM_FIELDS:
                try:
                    values = reader.get_child_by_name(theirs).read_array((slice(None), slice(None)))
                except Exception:
                    continue
                fields[ours] = np.nan_to_num(values.astype(np.float32)) * scale + add
            reader.close()
            omremote.done(path)
            # The dew point from the humidity (temp_pack's feels-like), the rain over the step an hour.
            if "rh" in fields and "2t" in fields:
                c = fields["2t"] - 273.15
                g = np.log(np.clip(fields.pop("rh"), 1, 100) / 100) + 17.625 * c / (243.04 + c)
                fields["2d"] = 243.04 * g / (17.625 - g) + 273.15
            if "rain" in fields:
                fields["rate"] = np.maximum(fields.pop("rain"), 0) / max(step - before, 1) if step > 0 else np.zeros_like(fields["10u"])
            before = step
            yield step, fields, ICON_OM_GRID


def gdps_url(run, name, step, host=None):
    return GDPS_URL.format(host=host or gdps_host[0], day=run.strftime("%Y%m%d"), hour=run.strftime("%H"), step=step, name=name)


def gdps_newest_run():
    """The newest 00/12 UTC GDPS run with its last step published (on the first server that has it)."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 12 * 12, minute=0, second=0, microsecond=0)
    for back in range(3):
        run = start - timedelta(hours=12 * back)
        for host in GDPS_HOSTS:
            if exists(gdps_url(run, "WindV_AGL-10m", GDPS_STEPS[-1], host)):
                gdps_host[0] = host
                print(f"GDPS from {host}", flush=True)
                return run
    raise SystemExit("No complete GDPS run found")


def grib_values(data):
    gid = ec.codes_new_from_message(data)
    try:
        return ec.codes_get_values(gid)
    finally:
        ec.codes_release(gid)


def icon_steps(run, steps):
    """ICON's wind and gust at each step, its triangles' values put on ICON_GRID (the three nearest
    cells, by distance)."""
    from scipy.spatial import cKDTree
    lat = np.radians(grib_values(bz2.decompress(fetch_bytes(icon_url(run, "clat", "CLAT")))))
    lon = np.radians(grib_values(bz2.decompress(fetch_bytes(icon_url(run, "clon", "CLON")))))
    tree = cKDTree(np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1))
    glat = np.radians(90 - np.arange(ICON_GRID["Nj"]) * 0.125)
    glon = np.radians(np.arange(ICON_GRID["Ni"]) * 0.125)
    la, lo = np.meshgrid(glat, glon, indexing="ij")
    points = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1).reshape(-1, 3)
    distance, nearest = tree.query(points, k=3, workers=-1)
    share = 1 / np.maximum(distance, 1e-9)
    share /= share.sum(axis=1, keepdims=True)
    shape = (ICON_GRID["Nj"], ICON_GRID["Ni"])

    def regular(values):
        return (values[nearest] * share).sum(axis=1).reshape(shape)

    def fetch(step):
        files = {}
        for param, folder, name in (("10u", "u_10m", "U_10M"), ("10v", "v_10m", "V_10M"), ("gust", "vmax_10m", "VMAX_10M"),
                                    ("2t", "t_2m", "T_2M"), ("2d", "td_2m", "TD_2M"), ("tp", "tot_prec", "TOT_PREC"),
                                    ("tcc", "clct", "CLCT"), ("msl", "pmsl", "PMSL")):
            data = fetch_bytes(icon_url(run, folder, name, step))
            if data is not None:
                files[param] = bz2.decompress(data)
        return step, files
    with ThreadPoolExecutor(max_workers=3) as pool:
        for step, files in pool.map(fetch, steps):
            fields = {param: regular(grib_values(data)) for param, data in files.items()}
            # The rain so far in metres (as ECMWF's), each step's from the one before.
            if "tp" in fields:
                fields["tp"] = fields["tp"] / 1000
            yield step, fields, ICON_GRID


def gdps_steps(run, steps):
    """GDPS's wind and the hour's highest gust at each step (its own 0.15° grid); the air, dew point,
    sea-level pressure, the hour's rain (mm, as the rate) and the cloud."""
    def fetch(step):
        files = {}
        for param, name in (("10u", "WindU_AGL-10m"), ("10v", "WindV_AGL-10m"), ("gust", "WindGust-Max_AGL-10m"),
                            ("2t", "AirTemp_AGL-2m"), ("2d", "DewPoint_AGL-2m"), ("msl", "Pressure_MSL"),
                            ("rate", "Precip-Accum1h_Sfc"), ("tcc", "TotalCloudCover_Sfc")):
            # Canada's servers reset connections now and then (18.10.2026: the whole run failed on
            # one file): a file that keeps failing leaves its field out, the hour without it.
            try:
                data = fetch_bytes(gdps_url(run, name, step))
            except (urllib.error.URLError, OSError) as error:
                print(f"GDPS +{step} h {name}: {error}", flush=True)
                data = None
            if data is not None:
                files[param] = data
        return step, files
    with ThreadPoolExecutor(max_workers=2) as pool:
        for step, files in pool.map(fetch, steps):
            if not files:
                yield step, {}, None
                continue
            fields, grid = ecmwf_map.decode(files)
            yield step, fields, grid


# MARK: Tiles

def tile_coordinates(z, x, y, sizes=None):
    """Latitude and longitude of each pixel centre of tile z/x/y (row 0 north)."""
    n, size = 2 ** z, (sizes or SIZES)[z]
    xs = (x + (np.arange(size) + 0.5) / size) / n
    ys = (y + (np.arange(size) + 0.5) / size) / n
    lon = xs * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * ys))))
    return np.meshgrid(lat, lon, indexing="ij")


class Sampler:
    """Bilinear from a global regular grid to every tile's pixels, the grid wrapped round at
    the date line (its first column again after its last)."""

    def __init__(self, grid, sizes=None):
        self.sizes = sizes or SIZES
        self.columns, self.rows = grid["Ni"], grid["Nj"]
        lat0, lon0 = grid["latitudeOfFirstGridPointInDegrees"], grid["longitudeOfFirstGridPointInDegrees"]
        dlat = grid["jDirectionIncrementInDegrees"] * (1 if grid["jScansPositively"] else -1)
        dlon = grid["iDirectionIncrementInDegrees"]
        self.tiles = {}
        # The zooms its sizes name (ECMWF 9 km's go to 4).
        for z in sorted(self.sizes):
            for x in range(2 ** z):
                for y in range(2 ** z):
                    lat, lon = tile_coordinates(z, x, y, self.sizes)
                    fi = np.mod(lon - lon0, 360) / dlon
                    fj = (lat - lat0) / dlat
                    self.tiles[(z, x, y)] = maplib.Bilinear(fi, fj, self.columns + 1, self.rows)

    def __call__(self, key, values):
        wrapped = np.concatenate([values, values[:, :1]], axis=1)
        return self.tiles[key](wrapped)


def encode(tiles, sizes=None):
    """Packed: "MGT1", the header's length, the header, the PNGs ({key: PNG bytes}); `sizes`:
    each zoom's tile side (the weather tiles' by default)."""
    sizes = sizes or SIZES
    header, offset, body = {}, 0, []
    for key, data in tiles.items():
        header["%d/%d/%d" % key] = [offset, len(data)]
        body.append(data)
        offset += len(data)
    # The tiles' offsets count from the end of the header (8 + its length).
    head = json.dumps({"sizes": {str(z): sizes[z] for z in sorted(sizes)}, "zooms": [min(sizes), max(sizes)], "tiles": header},
                      separators=(",", ":")).encode()
    return b"MGT1" + struct.pack("<I", len(head)) + head + b"".join(body)


def png(array, mode):
    buffer = io.BytesIO()
    Image.fromarray(array, mode).save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue()


def pack(sample, fields):
    """One hour: the wind's east and north parts (m/s x 4 + 128: smooth, so they pack small;
    where it comes from wraps round and didn't) and the gust (m/s x 5), as RGB tiles."""
    tiles = {}
    for key in sample.tiles:
        u, v = np.nan_to_num(sample(key, fields["10u"])), np.nan_to_num(sample(key, fields["10v"]))
        speed = np.hypot(u, v)
        gust = np.maximum(np.nan_to_num(sample(key, fields["gust"])) if "gust" in fields else speed, speed)
        rgb = np.stack([np.clip(np.rint(u * 4 + 128), 0, 255), np.clip(np.rint(v * 4 + 128), 0, 255),
                        np.clip(np.rint(gust * 5), 0, 255)], axis=-1).astype(np.uint8)
        tiles[key] = png(rgb, "RGB")
    return encode(tiles, sample.sizes)


def temp_pack(sample, fields):
    """The air as the Baltic temperature map: red (air °C + 40) x 3 + 1, green how it feels the
    same way (temp_map.feels_like: the wind chill to 10 °C, the apparent temperature from 20),
    blue (sea-level pressure hPa - 940) x 2; 0 = unknown."""
    tiles = {}
    for key in sample.tiles:
        celsius = sample(key, fields["2t"]) - 273.15
        u, v = sample(key, fields["10u"]), sample(key, fields["10v"])
        if "2d" in fields:
            dew = sample(key, fields["2d"]) - 273.15
            humidity = np.clip(np.exp(17.625 * dew / (243.04 + dew)) / np.exp(17.625 * celsius / (243.04 + celsius)), 0, 1)
        else:
            humidity = np.full_like(celsius, 0.6)
        feels = temp_map.feels_like(celsius, np.hypot(u, v), humidity)
        hpa = sample(key, fields["msl"]) / 100 if "msl" in fields else np.full_like(celsius, np.nan)
        rgb = np.stack([temp_map.encode(celsius), temp_map.encode(feels),
                        np.where(np.isfinite(hpa), np.clip(np.rint((hpa - 940) * 2), 1, 255), 0)], axis=-1).astype(np.uint8)
        tiles[key] = png(rgb, "RGB")
    return encode(tiles, sample.sizes)


def rain_pack(sample, fields, rate):
    """The rain as the Baltic rain map: red sqrt(mm an hour) x 50, green the cloud cover x 255."""
    tiles = {}
    for key in sample.tiles:
        mm = np.maximum(np.nan_to_num(sample(key, rate)), 0)
        cloud = np.nan_to_num(sample(key, fields["tcc"])) if "tcc" in fields else np.zeros_like(mm)
        if cloud.max() > 1.5:
            cloud = cloud / 100
        rgb = np.stack([np.clip(np.rint(np.sqrt(mm) * 50), 0, 255), np.clip(np.rint(cloud * 255), 0, 255),
                        np.zeros_like(mm)], axis=-1).astype(np.uint8)
        tiles[key] = png(rgb, "RGB")
    return encode(tiles, sample.sizes)


def cloud_pack(sample, fields):
    """The clouds' layers as the regional sets' (tiler.cloud_rgb): low, mid, high (%) x 2.55."""
    tiles = {}
    for key in sample.tiles:
        rgb = np.stack([np.clip(np.rint(np.nan_to_num(sample(key, fields[layer])) * 2.55), 0, 255) for layer in ("low", "mid", "high")],
                       axis=-1).astype(np.uint8)
        tiles[key] = png(rgb, "RGB")
    return encode(tiles, sample.sizes)


def sky_pack(sample, fields):
    """The rest of a place's weather (ECMWF 9 km's; the owner, 18.10.2026: the Weather tab off
    Open-Meteo), RGBA: red the relative humidity % x 2.54 + 1, green the visibility
    sqrt(min(m, 40 km) / 40 km) x 254 + 1 (0 = unknown), blue CAPE J/kg / 20, alpha 128 + the
    snow's share of the precipitation x 31, + 32 when showers are most of it, + 64 on land (alpha
    128 or more: the series' "has a value")."""
    tiles = {}
    for key in sample.tiles:
        celsius = sample(key, fields["2t"]) - 273.15
        dew = sample(key, fields["2d"]) - 273.15
        humidity = np.clip(np.exp(17.625 * dew / (243.04 + dew)) / np.exp(17.625 * celsius / (243.04 + celsius)), 0, 1)
        visibility = np.clip(np.nan_to_num(sample(key, fields["vis"]), nan=40000), 0, 40000)
        rain = np.maximum(np.nan_to_num(sample(key, fields["rain_step"])), 0) if "rain_step" in fields else np.zeros_like(celsius)
        wet = rain > 0.01
        snow = np.where(wet, np.clip(np.nan_to_num(sample(key, fields["snow"])) / np.maximum(rain, 1e-6), 0, 1), 0) if "snow" in fields else 0 * rain
        showers = np.where(wet, np.nan_to_num(sample(key, fields["showers"])) / np.maximum(rain, 1e-6) >= 0.5, False) if "showers" in fields else wet & False
        cape = np.clip(np.nan_to_num(sample(key, fields["cape"])) / 20, 0, 255) if "cape" in fields else 0 * rain
        land = sample(key, fields["land"]) >= 0.5 if "land" in fields else wet & False
        rgba = np.stack([np.where(np.isfinite(humidity), np.rint(humidity * 254) + 1, 0), np.rint(np.sqrt(visibility / 40000) * 254) + 1,
                         np.rint(cape), 128 + np.rint(snow * 31) + np.where(showers, 32, 0) + np.where(land, 64, 0)], axis=-1)
        tiles[key] = png(np.clip(rgba, 0, 255).astype(np.uint8), "RGBA")
    return encode(tiles, sample.sizes)


def land_pack(sample, mask):
    """The run's land (0 sea - 255 land) as grey tiles, once (it doesn't change with the hours)."""
    return encode({key: png(np.clip(np.rint(np.nan_to_num(sample(key, mask)) * 255), 0, 255).astype(np.uint8), "L")
                   for key in sample.tiles})


# MARK: Publishing

def upload(path, data):
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    for attempt in range(4):
        try:
            request = urllib.request.Request(UPLOAD + path, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "User-Agent": maplib.USER_AGENT})
            with urllib.request.urlopen(request, timeout=300) as reply:
                answer = reply.read().decode()
            # A pack made here kept on the home runner's disk as it goes up: the jobs that read it never download it.
            if path.endswith(".pack"):
                import packcache
                packcache.store("https://files.meteogust.com/tiles/" + path, data, downloaded=False)
            return answer
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))
        except (urllib.error.URLError, OSError):
            if attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def published(model):
    try:
        # With our own user agent: the file store's protection answers Python's own with 403
        # (15.10.2026: read as "nothing published", so a run made again reused its folder's name,
        # and phones could keep the cached packs of the first).
        request = urllib.request.Request(PUBLISHED.format(model=model) + "?t=%d" % time.time(),
                                         headers={"User-Agent": maplib.USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as reply:
            return json.load(reply).get("runName")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main():
    args = sys.argv[1:]
    if not args or args[0] not in MODELS:
        raise SystemExit(__doc__)
    model = args[0]
    info = MODELS[model]
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    sizes = MODEL_SIZES.get(model, SIZES)
    newest = {"gfs": gfs_map.newest_run, "ecmwf": ecmwf_map.newest_run, "icon": icon_newest_run, "gdps": gdps_newest_run}
    # ECMWF at 9 km from Open-Meteo's open data (hourly); when that can't be read ECMWF's own 9 km files
    # (the owner, 21.10.2026: free from the 20.10.2026 06Z run, 3-hourly, two hours after real time),
    # then its 0.25° ones.
    nine = model == "ecmwf" and "--open-data" not in args
    own9 = False
    if nine:
        try:
            run = ecmwf9_newest_run()
        except BaseException as error:
            print(f"ECMWF 9 km: {error}; ECMWF's own 9 km files instead", flush=True)
            nine = False
    # A 06/18 UTC run (6 days): its hours, then the long run's (with_tail); its spots are
    # ecmwf_short_points.py's (it keeps the long run's later hours there too).
    short = nine and run.hour % 12 != 0
    if model == "ecmwf" and not nine:
        try:
            run = ecmwf_map.newest_run("0p1")
            own9 = True
        except BaseException as error:
            print(f"ECMWF's own 9 km files: {error}; its 0.25° ones instead", flush=True)
    icon_om = None
    if model == "icon" and "--dwd" not in args:
        try:
            run, icon_om = icon_om_newest_run()
        except BaseException as error:
            print(f"ICON open data: {error}; DWD's own files instead", flush=True)
    if own9:
        info = {"name": "ECMWF 9 km", "credit": "ECMWF open data (CC BY 4.0)", "gust": "maximum since the previous step"}
    elif not nine and icon_om is None:
        if model == "ecmwf":
            sizes = SIZES
            info = {"name": "ECMWF 0.25°", "credit": "ECMWF open data (CC BY 4.0)", "gust": "maximum since the previous step"}
        run = newest[model]()
    run_name = run.strftime("%Y%m%dT%HZ") + ("-9km" if nine else "-od9" if own9 else "-od" if icon_om else "")
    if "--check" in args:
        print(run_name)
        return
    if local is None and "--force" not in args and published(model) == run_name:
        print(f"{model}: {run_name} is published already")
        return
    steps = {"gfs": GFS_STEPS, "ecmwf": ecmwf_map.STEPS, "icon": ICON_STEPS, "gdps": GDPS_STEPS}[model]
    if nine:
        steps = None
    if "--steps" in args:
        steps = [int(s) for s in args[args.index("--steps") + 1].split(",")]
    if icon_om is not None:
        keys = icon_om
        if "--steps" in args:
            keys = [k for k in keys if int((datetime.strptime(k.rsplit("/", 1)[-1][:-3], "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
                                            - run).total_seconds() // 3600) in steps]
        source = icon_om_steps(run, keys)
    else:
        source = (ecmwf9_steps if nine else (lambda r, s: ecmwf_steps(r, s, "0p1")) if own9
                  else {"gfs": gfs_steps, "ecmwf": ecmwf_steps, "icon": icon_steps, "gdps": gdps_steps}[model])(run, steps)
    sample, land, frames, size, started = None, False, [], 0, time.time()
    # The step and precipitation before (ECMWF's is the total since the run's start).
    previous = [0, None]

    def put(name, data):
        if local:
            (local / name).parent.mkdir(parents=True, exist_ok=True)
            (local / name).write_bytes(data)
        else:
            upload(f"{model}/{name}", data)

    # The spots' values from the model's own cells as each hour goes by (spot_cells.py; the owner,
    # 18.10.2026); the tiles go on without them if they fail.
    import spot_cells
    try:
        if short:
            raise RuntimeError("a 06/18 UTC run: the spots are ecmwf_short_points.py's")
        # GFS's spots' wind from its 0.13° grid (gfs013_points.py), as Open-Meteo's; here its weather
        # and the 0.25° wind as points/gfs-025.json, whose gusts that takes (the 0.13° files have none).
        cells = spot_cells.SpotCells("gfs-025" if model == "gfs" else model, {"gfs": "gfs_global", "ecmwf": "ecmwf_ifs", "icon": "icon_global",
                                             "gdps": "gem_global"}[model], weather=f"{model}-wx")
    except Exception as error:
        print(f"{model}: the spots' cells left out ({error})", flush=True)
        cells = None

    # Each hour's layers made side by side and sent while the next hour is made (the owner, 21.10.2026:
    # ECMWF 9 km's run took 100 minutes, its five layers one after another, about 50 s an hour; the
    # images' compression and NumPy let go of Python's lock, so threads use the runner's cores). At most
    # 12 uploads waiting (about 80 MB an hour in memory). A layer other than the wind that can't be made
    # or sent is left out of its hour, as before; the wind's stops the run.
    makers = ThreadPoolExecutor(max_workers=5)
    senders = ThreadPoolExecutor(max_workers=6)
    sending = []

    def settle(limit):
        nonlocal size
        while len(sending) > limit:
            future, frame, key, name, length = sending.pop(0)
            try:
                future.result()
                if key != "file":
                    frame[key] = name
                size += length
            except (urllib.error.URLError, OSError) as error:
                if key == "file":
                    raise
                print(f"{model} {name}: not uploaded ({error})", flush=True)

    suffixes = {"file": "", "temp": "-temp", "rain": "-rain", "cloud": "-cloud", "sky": "-sky"}
    for step, fields, grid in source:
        if "10u" not in fields or "10v" not in fields:
            print(f"{model} step {step}: no wind, skipped")
            continue
        if sample is None:
            sample = Sampler(grid, sizes)
        if not land and "lsm" in fields:
            put(f"{run_name}/land.pack", land_pack(sample, fields["lsm"]))
            land = True
        t = int((run + timedelta(hours=step)).timestamp())
        # ECMWF: the world's temperature and rain too (the precipitation since the run's start,
        # so each step's rain is its difference from the step before, per hour).
        rate = None
        if "tp" in fields:
            hours = step - previous[0] if previous[1] is not None else 0
            rate = (fields["tp"] - previous[1]) * 1000 / hours if hours > 0 else np.zeros_like(fields["tp"])
            previous[0], previous[1] = step, fields["tp"]
        elif "rate" in fields:
            rate = fields["rate"]
        jobs = {"file": makers.submit(pack, sample, fields)}
        if "2t" in fields:
            jobs["temp"] = makers.submit(temp_pack, sample, fields)
        if rate is not None:
            jobs["rain"] = makers.submit(rain_pack, sample, fields, rate)
        # The clouds' layers (ECMWF 9 km's), as the regional sets' cloud packs.
        if "low" in fields:
            jobs["cloud"] = makers.submit(cloud_pack, sample, fields)
        # The rest of a place's weather (ECMWF 9 km's: humidity, visibility, thunder, snow, showers).
        if "vis" in fields and "2d" in fields:
            jobs["sky"] = makers.submit(sky_pack, sample, fields)
        if cells is not None:
            try:
                if "_native" in fields:
                    cells.add_native(t, fields["_native"])
                else:
                    cells.add_regular(t, grid, fields, rate)
            except Exception as error:
                print(f"{model} step {step}: the spots' cells left out ({error})", flush=True)
                cells = None
        frame = {"time": t, "file": f"{run_name}/{t}.pack"}
        for key, job in jobs.items():
            name = f"{run_name}/{t}{suffixes[key]}.pack"
            try:
                data = job.result()
            except Exception as error:
                if key == "file":
                    raise
                print(f"{model} step {step}: {key} not made ({error})", flush=True)
                continue
            sending.append((senders.submit(put, name, data), frame, key, name, len(data)))
        frames.append(frame)
        settle(12)
    settle(0)
    makers.shutdown()
    senders.shutdown()
    if not frames:
        raise SystemExit(f"{model}: no frames")
    if short:
        own = len(frames)
        frames = with_tail(frames, published_index(model))
        print(f"{model} {run_name}: {own} hours of its own, {len(frames) - own} of the run before after them", flush=True)
    index = {
        "model": info["name"], "credit": info["credit"], "run": run.strftime("%Y-%m-%dT%H:%MZ"), "runName": run_name,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "tileSizes": {str(z): sizes[z] for z in sorted(sizes)},
        "zooms": [min(sizes), max(sizes)], "projection": "Web Mercator tiles (z/x/y, y from the north)",
        "encoding": {"red": "wind towards the east m/s x 4 + 128", "green": "wind towards the north m/s x 4 + 128",
                     "blue": "gust m/s x 5 (" + info["gust"] + ")"},
        "land": f"{run_name}/land.pack" if land else None,
        "frames": frames,
    }
    body = json.dumps(index, separators=(",", ":")).encode()
    if local:
        (local / "index.json").write_bytes(body)
        print(f"{model} {run_name}: {len(frames)} packs, {size / 1e6:.0f} MB, {time.time() - started:.0f} s (local)")
    else:
        print(upload(f"{model}/index.json", body).strip())
        print(f"{model} {run_name}: {len(frames)} packs, {size / 1e6:.0f} MB, {time.time() - started:.0f} s")
    if cells is not None:
        try:
            cells.publish(run_name, str(local / "points") if local else None)
        except Exception as error:
            print(f"{model}: the spots' cells not published ({error})", flush=True)


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up: their exit can crash Python after the work is published
    # ("double free or corruption", 18.10.2026), failing the run and the workflow's next step.
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
