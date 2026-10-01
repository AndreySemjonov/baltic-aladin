"""Wind map frames for the Latvian coast from DMI's HARMONIE DINI 2 km ("HARM-DK", DMI open data).

DMI publishes each forecast hour as one GRIB2 file of about 620 MB (all of its surface
fields over a domain from Greenland to Russia). We only need 10 m wind speed, direction
and gust in our box, so this reads the files' headers with small HTTP range requests and
then downloads only the rows of those three fields that cross the box (about 4 MB per
hour). The fields are simply packed, so the rows decode without the rest of the field.
DMI's direction is relative to true north (its u/v components are relative to the grid).

Same box, pixels and land mask as the MET Nordic map; format in maplib.py.

    python dmi_map.py site/harm-dk
"""
import json
import struct
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import aladin_map
import maplib
import metnordic_map

ITEMS = "https://opendataapi.dmi.dk/v1/forecastdata/collections/harmonie_dini_sf/items"
HOURS = 60  # each run goes 60 hours ahead: 61 files
SOUTH, NORTH, WEST, EAST = metnordic_map.SOUTH, metnordic_map.NORTH, metnordic_map.WEST, metnordic_map.EAST
# DINI's grid (Lambert conformal on a sphere), as in its GRIB files.
GRID = {"Nx": 1906, "Ny": 1606, "DxInMetres": 2000.0, "DyInMetres": 2000.0, "LoVInDegrees": -8.0,
        "LaDInDegrees": 55.5, "Latin1InDegrees": 55.5, "Latin2InDegrees": 55.5,
        "latitudeOfFirstGridPointInDegrees": 39.671, "longitudeOfFirstGridPointInDegrees": 334.578,
        "radius": 6371229.0}
# (discipline, category, number, surface type, surface value) of the fields we read.
FIELDS = {"speed": (0, 2, 1, 103, 10), "direction": (0, 2, 0, 103, 10), "gust": (0, 2, 22, 103, 10)}


def get(url, start=None, end=None, timeout=300):
    headers = {"User-Agent": maplib.USER_AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as reply:
                return reply.read()
        except OSError:
            if attempt == 2:
                raise


def files_of(run):
    """{valid time: file URL} of a run, from DMI's catalogue."""
    data = json.loads(get(f"{ITEMS}?modelRun={run.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=300"))
    out = {}
    for item in data.get("features", []):
        valid = datetime.strptime(item["properties"]["datetime"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        out[valid] = item["asset"]["data"]["href"]
    return out


def newest_run():
    """The newest run (every 3 hours) whose 61 files are all published."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=now.hour // 3 * 3, minute=0, second=0, microsecond=0)
    for back in range(8):
        run = start - timedelta(hours=3 * back)
        files = files_of(run)
        if len(files) >= HOURS + 1:
            return run, files
    raise SystemExit("No complete HARMONIE DINI run found")


def signed16(raw):
    """GRIB2 signed integers: the top bit is the sign."""
    value = int.from_bytes(raw, "big")
    return -(value & 0x7FFF) if value & 0x8000 else value


def header(url, offset):
    """What the GRIB2 message at `offset` holds, read from its first bytes: its field, its
    packing and where its data starts."""
    size = 4096
    while True:
        raw = get(url, offset, offset + size - 1)
        if raw[:4] != b"GRIB" or raw[7] != 2:
            raise ValueError(f"no GRIB2 message at {offset}")
        info = {"discipline": raw[6], "total": int.from_bytes(raw[8:16], "big")}
        pos = 16
        while pos + 5 <= len(raw):
            length, number = int.from_bytes(raw[pos:pos + 4], "big"), raw[pos + 4]
            if number == 4:
                info.update(category=raw[pos + 9], parameter=raw[pos + 10], surface=raw[pos + 22],
                            level=int.from_bytes(raw[pos + 24:pos + 28], "big"))
            elif number == 5:
                info.update(points=int.from_bytes(raw[pos + 5:pos + 9], "big"),
                            template=int.from_bytes(raw[pos + 9:pos + 11], "big"),
                            reference=struct.unpack(">f", raw[pos + 11:pos + 15])[0],
                            binary=signed16(raw[pos + 15:pos + 17]), decimal=signed16(raw[pos + 17:pos + 19]),
                            bits=raw[pos + 19])
            elif number == 6:
                info["bitmap"] = raw[pos + 5]
            elif number == 7:
                info["data"] = offset + pos + 5
                return info
            pos += length
        size *= 4  # a long section 2: read more


def key(info):
    return (info["discipline"], info.get("category"), info.get("parameter"), info.get("surface"), info.get("level"))


def locate(url, hint):
    """Headers of our three fields in one file. `hint` holds the offsets found in an earlier
    file of the run (usually the same); otherwise the file is walked message by message."""
    found = {}
    for name, offset in hint.items():
        try:
            info = header(url, offset)
        except (ValueError, OSError):
            continue
        if key(info) == FIELDS[name]:
            found[name] = dict(info, offset=offset)
    if len(found) < len(FIELDS):
        found, offset, wanted = {}, 0, {v: k for k, v in FIELDS.items()}
        while len(found) < len(FIELDS):
            try:
                info = header(url, offset)
            except ValueError:
                break  # end of the file: a field is missing (the first hour has no gust)
            name = wanted.get(key(info))
            if name:
                found[name] = dict(info, offset=offset)
            offset += info["total"]
    return found


def rows(url, info, r0, r1):
    """Rows r0..r1 of a simply packed field (all columns), decoded."""
    if info.get("template") != 0 or info.get("bitmap", 255) != 255 or info["bits"] % 8:
        raise ValueError(f"unexpected packing {info.get('template')}/{info.get('bits')}")
    width, nb = GRID["Nx"], info["bits"] // 8
    start = info["data"] + r0 * width * nb
    raw = np.frombuffer(get(url, start, start + (r1 - r0 + 1) * width * nb - 1), dtype=np.uint8).reshape(-1, nb)
    packed = np.zeros(len(raw), dtype=np.uint32)
    for byte in range(nb):
        packed = (packed << 8) | raw[:, byte]
    values = (info["reference"] + packed.astype(np.float64) * 2.0 ** info["binary"]) / 10.0 ** info["decimal"]
    return values.reshape(r1 - r0 + 1, width).astype(np.float32)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/harm-dk")
    run, files = newest_run()
    (lat, lon), width, height = maplib.output_grid(SOUTH, NORTH, WEST, EAST, metnordic_map.KM_PER_PIXEL)
    fi, fj = aladin_map.lambert(lat, lon, GRID)
    c0, c1 = max(int(np.floor(fi.min())), 0), min(int(np.ceil(fi.max())) + 1, GRID["Nx"] - 1)
    r0, r1 = max(int(np.floor(fj.min())), 0), min(int(np.ceil(fj.max())) + 1, GRID["Ny"] - 1)
    sample = maplib.Bilinear(fi - c0, fj - r0, c1 - c0 + 1, r1 - r0 + 1)

    times = sorted(t for t in files if t >= run)
    # One file's offsets serve as the hint for the others (the layout rarely changes).
    hint = {name: info["offset"] for name, info in locate(files[times[-1]], {}).items()}

    def fetch(valid):
        url = files[valid]
        found = locate(url, hint)
        if "speed" not in found or "direction" not in found:
            return valid, None
        return valid, {name: rows(url, info, r0, r1)[:, c0:c1 + 1] for name, info in found.items()}

    out.mkdir(parents=True, exist_ok=True)
    maplib.save_land(out / "land.png", metnordic_map.land_fraction(lat, lon))
    run_name = run.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for valid, fields in pool.map(fetch, times):
            if fields is None:
                continue
            speed, degrees = fields["speed"], np.radians(fields["direction"])
            u, v = sample(-speed * np.sin(degrees)), sample(-speed * np.cos(degrees))
            interpolated = sample(speed)
            gust = sample(fields["gust"]) if "gust" in fields else interpolated
            t = int(valid.timestamp())
            maplib.save_frame(folder / f"{t}.png", interpolated, maplib.direction_from(u, v), gust)
            frames.append({"time": t, "file": f"{run_name}/{t}.png"})
    frames.sort(key=lambda frame: frame["time"])
    manifest = maplib.write_manifest(out, "HARM-DK 2 km",
                                     "DMI, HARMONIE DINI forecast, open data (CC BY 4.0): https://opendatadocs.dmi.govcloud.dk/",
                                     run, (SOUTH, NORTH, WEST, EAST), width, height, frames,
                                     gust_note="gust m/s x 5 (maximum of the last hour)")
    size = sum(p.stat().st_size for p in folder.glob("*.png"))
    print(f"HARM-DK run {manifest['run']}: {len(frames)} frames {width}x{height}, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
