"""The world's tides along the coasts, for the app's tide row and tide map (the owner, 16.10.2026:
"better accuracy always better", "for the whole world"): the harmonic constituents of EOT20, the
empirical ocean tide model from satellite altimetry (DGFI-TUM, Hart-Davis et al. 2021,
doi:10.17882/79489, CC BY 4.0; 17 constituents on a 1/8 degree grid), for every sea cell within
half a degree of land, in one pack per zoom-5 tile (tiles/tides/5-x-y.pack) and an index. Made
once (the constituents don't change); the phones predict any day from them.

A pack: b"TID1", the header's length (uint32) and the header (JSON: the grid's origin and step,
the constituents' order, the count), then each cell: its grid row and column (uint16 each) and per
constituent its amplitude in mm and its phase (Greenwich lag) in hundredths of a degree (uint16
each), little-endian.

Also check.json (the run's artifact, not uploaded): pyTMD's own prediction (FES nodal corrections,
the major constituents only, as the phones) at a few ports for a week, for the app's tests.

    python tides_world.py              # download, make, upload
    python tides_world.py --local out  # make into a folder, no upload
"""
import io
import json
import math
import os
import pathlib
import struct
import sys
import time
import urllib.request
import zipfile

import numpy as np
from netCDF4 import Dataset
from scipy import ndimage

URL = "https://www.seanoe.org/data/00683/79489/data/85762.zip"
CONSTITUENTS = ["M2", "S2", "N2", "K2", "K1", "O1", "P1", "Q1", "2N2", "J1", "M4", "MF", "MM", "S1", "SA", "SSA", "T2"]
NEAR = 4  # cells (half a degree) from land
CREDIT = "EOT20 (DGFI-TUM, Hart-Davis et al. 2021), CC BY 4.0"
PORTS = {"tarifa": (36.007, -5.612), "brest": (48.38, -4.495), "saint-malo": (48.64, -2.03), "cork": (51.80, -8.25),
         "dover": (51.11, 1.33), "vlissingen": (51.44, 3.59)}
HERE = pathlib.Path(__file__).parent


def download(folder):
    target = folder / "eot20.zip"
    if not target.exists():
        print("downloading EOT20 (about 2 GB)", flush=True)
        request = urllib.request.Request(URL, headers={"User-Agent": "MeteoGust tides (data.meteogust.com)"})
        with urllib.request.urlopen(request, timeout=600) as reply, open(target, "wb") as out:
            while True:
                chunk = reply.read(1 << 22)
                if not chunk:
                    break
                out.write(chunk)
    with zipfile.ZipFile(target) as archive:
        archive.extractall(folder)
    # Inner zips (the ocean tides may come packed again).
    for inner in folder.rglob("*.zip"):
        if inner != target:
            with zipfile.ZipFile(inner) as archive:
                archive.extractall(inner.parent)
    files = {}
    for path in folder.rglob("*_ocean_eot20.nc"):
        files[path.name.split("_")[0].upper()] = path
    missing = [c for c in CONSTITUENTS if c not in files]
    if missing:
        raise SystemExit(f"EOT20 files missing: {missing}")
    return files


def tile_of(lat, lon):
    n = 32
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(max(min(lat, 85.05), -85.05)))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def main():
    args = sys.argv[1:]
    local = pathlib.Path(args[args.index("--local") + 1]) if "--local" in args else None
    work = HERE / "eot20-data"
    work.mkdir(exist_ok=True)
    files = download(work)
    amps, phases = [], []
    lat = lon = None
    for name in CONSTITUENTS:
        with Dataset(files[name]) as data:
            if lat is None:
                lat, lon = np.array(data["lat"][:], dtype=np.float64), np.array(data["lon"][:], dtype=np.float64)
            a = np.ma.filled(data["amplitude"][:], 0).astype(np.float64)  # cm
            p = np.ma.filled(data["phase"][:], 0).astype(np.float64)      # degrees
        if a.shape != (lat.size, lon.size):
            a, p = a.T, p.T
        amps.append(a)
        phases.append(p)
    amp = np.stack(amps)
    phase = np.stack(phases)
    sea = (amp[0] != 0) | (phase[0] != 0)
    land = ~sea
    near = sea & ndimage.binary_dilation(land, iterations=NEAR)
    rows, cols = np.nonzero(near)
    print(f"grid {lat.size} x {lon.size}, {sea.sum()} sea cells, {rows.size} within {NEAR} cells of land", flush=True)
    step = float(lat[1] - lat[0])
    groups = {}
    for r, c in zip(rows, cols):
        lo = lon[c] if lon[c] <= 180 else lon[c] - 360
        groups.setdefault(tile_of(lat[r], lo), []).append((r, c))
    header = {"lat0": float(lat[0]), "lon0": float(lon[0]), "step": step, "constituents": CONSTITUENTS,
              "units": "amplitude mm, phase 0.01 degree (Greenwich lag)", "credit": CREDIT}
    names = []
    total = 0
    out_dir = local / "tides" if local else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for (x, y), cells in sorted(groups.items()):
        head = json.dumps(dict(header, count=len(cells)), separators=(",", ":")).encode()
        body = bytearray()
        for r, c in cells:
            body += struct.pack("<HH", int(r), int(c))
            for k in range(len(CONSTITUENTS)):
                a_mm = int(round(min(amp[k, r, c] * 10, 65535)))
                ph = int(round((phase[k, r, c] % 360) * 100)) % 36000
                body += struct.pack("<HH", a_mm, ph)
        data = b"TID1" + struct.pack("<I", len(head)) + head + bytes(body)
        name = f"5-{x}-{y}.pack"
        names.append(name[:-5])
        total += len(data)
        if out_dir:
            (out_dir / name).write_bytes(data)
        else:
            upload("tides/" + name, data)
    index = {"version": "eot20-1", "made": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "tiles": names,
             "constituents": CONSTITUENTS, "credit": CREDIT, "near": NEAR}
    body = json.dumps(index, separators=(",", ":")).encode()
    if out_dir:
        (out_dir / "index.json").write_bytes(body)
    else:
        upload("tides/index.json", body)
    print(f"{len(names)} tiles, {total / 1e6:.1f} MB", flush=True)
    table = arguments_table()
    if out_dir:
        (out_dir / "args.json").write_bytes(table)
    else:
        upload("tides/args.json", table)
    check(work, local or HERE)


def upload(path, data):
    # world_tiles' upload without its map imports (eccodes is not installed here).
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    for attempt in range(4):
        try:
            request = urllib.request.Request("https://data.meteogust.com/tiles/upload/" + path, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token,
                                                      "User-Agent": "MeteoGust tides (data.meteogust.com)"})
            with urllib.request.urlopen(request, timeout=300) as reply:
                return reply.read().decode()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def arguments_table():
    """The constituents' astronomical arguments by month, 2026-2036, for the phones' prediction
    (pyTMD, FES corrections, as the check): each month's start (Unix s), and per constituent its
    phase G at that start (degrees), its nodal angle u (degrees) and factor f; the speeds (degrees an
    hour). Between two months the phones carry G on at its speed and blend u and f."""
    import pyTMD
    names = [c.lower() for c in CONSTITUENTS]
    starts, G0, U, F = [], [], [], []
    for year in range(2026, 2037):
        for month in range(1, 13):
            unix = int(time.mktime(time.strptime(f"{year}-{month:02d}-01", "%Y-%m-%d")) - time.timezone)
            starts.append(unix)
    starts.append(int(time.mktime(time.strptime("2037-01-01", "%Y-%m-%d")) - time.timezone))
    mjd = np.array(starts, dtype=np.float64) / 86400.0 + 40587.0
    pu, pf, G = pyTMD.constituents.arguments(mjd, names, corrections="FES")
    pu1, pf1, G1 = pyTMD.constituents.arguments(mjd + 1.0 / 24, names, corrections="FES")
    speed = np.mod(np.asarray(G1)[0] - np.asarray(G)[0], 360.0)
    table = {"constituents": CONSTITUENTS, "starts": starts, "speed": [round(float(v), 7) for v in speed],
             "g": [[round(float(v) % 360, 4) for v in row] for row in np.asarray(G)],
             "u": [[round(float(np.degrees(v)), 4) for v in row] for row in np.asarray(pu)],
             "f": [[round(float(v), 5) for v in row] for row in np.asarray(pf)],
             "corrections": "FES", "source": "pyTMD"}
    return json.dumps(table, separators=(",", ":")).encode()


def check(work, folder):
    """pyTMD's prediction at the ports, hourly for 8 days from 16.10.2026 00:00 UTC (the app's tests)."""
    try:
        import pyTMD
    except ImportError:
        print("pyTMD missing: no check", flush=True)
        return
    root = work / "pytmd"
    target = root / "EOT20" / "ocean_tides"
    target.mkdir(parents=True, exist_ok=True)
    for path in work.rglob("*_ocean_eot20.nc"):
        link = target / path.name
        if not link.exists():
            try:
                link.symlink_to(path.resolve())
            except OSError:
                link.write_bytes(path.read_bytes())
    start = 1792108800  # 2026-10-16T00:00Z
    hours = np.arange(0, 8 * 24)
    seconds_since_2000 = (start - 946684800) + hours * 3600.0
    out = {"start": start, "step": 3600, "corrections": "FES", "infer_minor": False, "ports": {}}
    for name, (la, lo) in PORTS.items():
        try:
            tide = pyTMD.compute.tide_elevations(np.full(hours.size, lo), np.full(hours.size, la), seconds_since_2000 / 86400.0,
                                                 directory=root, model="EOT20", epoch=(2000, 1, 1, 0, 0, 0), type="time series",
                                                 standard="UTC", method="bilinear", extrapolate=True, cutoff=20,
                                                 infer_minor=False, corrections="FES")
            values = np.asarray(tide).ravel()
            out["ports"][name] = {"lat": la, "lon": lo, "cm": [round(float(v), 2) for v in values]}
            print(name, "ok", flush=True)
        except Exception as error:
            print(name, "failed:", error, flush=True)
    (pathlib.Path(folder) / "check.json").write_text(json.dumps(out), encoding="utf-8")


if __name__ == "__main__":
    main()
