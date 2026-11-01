"""MET Norway's Nordic radar composite laid over our OPERA radar packs, for the dev map's lab (the owner, 01.11.2026:
"we are trying to fix holes, but we are not focusing on main issue"; "lets use web version as our lab ... if others can
provide clean data we have too"). At 08:30 UTC on 01.11 OPERA drew Bålsta's interference as a 200 km line over the sea
south-east of Stockholm; this composite had none.

Their composite (thredds.met.no remotesensing/reflectivity-nordic, CC BY 4.0, MET Norway): every 5 minutes, 1 km, the
radars of Norway, Sweden and Finland (36), clutter-filtered and corrected for blocking ("clfilter ... clcorr-block"), with
flags for no data and sea, ground and other clutter, and the phase (rain, sleet, snow). One file a day over OPeNDAP;
about 10 minutes behind.

Each 5-minute time: their reflectivity where they see (no-data off; any clutter flag dry) turned into our bytes as the
radar Worker does (radar.js mapRate: the alerts' table from 14.5 dBZ, light rain from 7, sqrt(mm/h) x 50), the nearest of
their pixels to each zoom-6 pixel, over our published OPERA pack of the same time (OPERA where they don't see); zooms 5-3
again above the changed tiles, two by two as radar.js; written as DIR/radar/<time>.pack and DIR/radar/index.json, the
layout serve.py --local reads. Their light rain isn't limited to near real rain (no keepFringe): cleaned, it shouldn't
need it, which is part of what the lab shows.

    python nordic_radar.py --local DIR [--hours 3] [--loop]   # the last hours; --loop: new times every 2 minutes
        [--near 1]          # OPERA's echo kept where theirs is dry but has rain (byte 1+; 16: 0.1 mm/h) within ~5 km
        [--minutes 20,50]   # only those minutes (the airports' reports), for scoring
"""
import io
import json
import math
import struct
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

THREDDS = "https://thredds.met.no/thredds/dodsC/remotesensing/reflectivity-nordic/"
NAME = "yrwms-nordic.mos.pcappi-0-dbz.noclass-clfilter-novpr-clcorr-block.nordiclcc-1000."
OURS = "https://files.meteogust.com/tiles/radar/"
AGENT = {"User-Agent": "MeteoGust-devmap/1"}
TOP, ZOOMS, SIZE = 6, (3, 4, 5, 6), 256


def map_rate(dbz):
    """radar.js mapRate on an array: mm/h from dBZ (0 under 7)."""
    dbz = np.nan_to_num(dbz, nan=-99.0)
    low = 0.06 * (0.1 / 0.06) ** ((dbz - 7) / 7.5)
    mid = 0.1 * (1 / 0.1) ** ((dbz - 14.5) / 13)
    high = 1 * (4 / 1) ** ((dbz - 27.5) / 9)
    return np.where(dbz < 7, 0.0, np.where(dbz < 14.5, low, np.where(dbz < 27.5, mid, high)))


def rate_bytes(dbz):
    mm = map_rate(dbz)
    return np.where(mm > 0, np.clip(np.round(np.sqrt(mm) * 50), 1, 255), 0).astype(np.uint8)


def read_pack(data):
    n = struct.unpack("<I", data[4:8])[0]
    head = json.loads(data[8:8 + n])
    tiles = {}
    for key, (o, k) in head["tiles"].items():
        tiles[key] = np.asarray(Image.open(io.BytesIO(data[8 + n + o:8 + n + o + k])).convert("LA")).copy()
    return tiles


def write_pack(tiles):
    blobs, header, offset = [], {}, 0
    for key, pixels in tiles.items():
        out = io.BytesIO()
        Image.fromarray(pixels, "LA").save(out, "PNG")
        png = out.getvalue()
        header[key] = [offset, len(png)]
        blobs.append(png)
        offset += len(png)
    head = json.dumps({"sizes": {str(z): SIZE for z in ZOOMS}, "zooms": [ZOOMS[0], TOP], "tiles": header},
                      separators=(",", ":")).encode()
    return b"MGT1" + struct.pack("<I", len(head)) + head + b"".join(blobs)


class Nordic:
    """One day's file over OPeNDAP and, once, where each zoom-6 pixel of the tiles it covers falls on its grid."""

    def __init__(self, day):
        import xarray as xr
        from pyproj import Transformer
        self.data = xr.open_dataset(f"{THREDDS}{day:%Y/%m}/{NAME}{day:%Y%m%d}.nc")
        xc, yc = self.data["Xc"].values, self.data["Yc"].values
        lat, lon = self.data["lat"].values, self.data["lon"].values
        to = Transformer.from_crs("EPSG:4326", self.data["projection_lambert"].attrs["proj4"], always_xy=True)
        n = 1 << TOP
        tx = lambda lo: (lo + 180) / 360 * n
        ty = lambda la: (1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * n
        self.where = {}
        for x in range(int(tx(np.nanmin(lon))), int(tx(np.nanmax(lon))) + 1):
            for y in range(int(ty(np.nanmax(lat))), int(ty(np.nanmin(lat))) + 1):
                px = (x + (np.arange(SIZE) + 0.5) / SIZE) / n * 360 - 180
                py = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y + (np.arange(SIZE) + 0.5) / SIZE) / n))))
                lo, la = np.meshgrid(px, py)
                gx, gy = to.transform(lo, la)
                # shortcut: the nearest of their 1 km pixels (radar.js means OPERA's 2 x 2), fine for a look; mean if it matters.
                i = np.round((gy - yc[0]) / (yc[1] - yc[0])).astype(int)
                j = np.round((gx - xc[0]) / (xc[1] - xc[0])).astype(int)
                inside = (i >= 0) & (i < len(yc)) & (j >= 0) & (j < len(xc))
                if inside.any():
                    self.where[f"{TOP}/{x}/{y}"] = (np.where(inside, i, 0), np.where(inside, j, 0), inside)

    def times(self):
        return [int(t.astype("datetime64[s]").astype(int)) for t in self.data["time"].values]

    def tiles(self, when):
        """{zoom-6 key: (bytes, seen)} at a time."""
        s = self.data.sel(time=np.datetime64(datetime.fromtimestamp(when, timezone.utc).replace(tzinfo=None)))
        dbz = s["equivalent_reflectivity_factor"].values
        seen = s["is_nodata"].values == 0
        clutter = (s["is_groundclutter"].values > 0) | (s["is_seaclutter"].values > 0) | (s["is_otherclutter"].values > 0)
        rate = np.where(clutter, 0, rate_bytes(dbz)).astype(np.uint8)
        return {key: (rate[i, j], seen[i, j] & inside) for key, (i, j, inside) in self.where.items()}


def merged(nordic, when, near=0):
    """Our OPERA pack at `when` with their composite over it, or None when ours isn't there. `near` (a rate byte, 0 off):
    where theirs is dry, OPERA's echo kept within 4 zoom-6 pixels (about 5 km) of theirs at `near` or more: the light
    rain their cleaning drops next to real rain (the first score, 01.11: their drizzle caught 13 % against OPERA's 22 %),
    OPERA's lone noise still out."""
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{OURS}{when}.pack", headers=AGENT), timeout=60) as r:
            tiles = read_pack(r.read())
    except Exception as e:
        print(f"  {when}: no OPERA pack ({e})")
        return None
    changed = set()
    for key, (rate, seen) in nordic.tiles(when).items():
        if not seen.any():
            continue
        px = tiles.get(key, np.zeros((SIZE, SIZE, 2), np.uint8))
        if near:
            from scipy.ndimage import maximum_filter
            # shortcut: "near" looks within this tile only; an echo across a tile's edge may be left out.
            close = maximum_filter((rate >= near) & seen, size=9) & (px[..., 1] > 0)
            rate = np.where((rate == 0) & close, px[..., 0], rate)
        px[..., 0] = np.where(seen, rate, px[..., 0])
        px[..., 1] = np.where(seen, 255, px[..., 1])
        tiles[key] = px
        changed.add(key)
    for z in range(TOP - 1, ZOOMS[0] - 1, -1):
        above = {f"{z}/{int(k.split('/')[1]) // 2}/{int(k.split('/')[2]) // 2}" for k in changed}
        for key in above:
            _, x, y = (int(v) for v in key.split("/"))
            px = np.zeros((SIZE, SIZE, 2), np.uint8)
            for q in range(4):
                kid = tiles.get(f"{z + 1}/{x * 2 + (q & 1)}/{y * 2 + (q >> 1)}")
                if kid is None:
                    continue
                v = kid[..., 0].reshape(SIZE // 2, 2, SIZE // 2, 2).astype(np.float32)
                a = (kid[..., 1] > 0).reshape(SIZE // 2, 2, SIZE // 2, 2)
                cnt = a.sum(axis=(1, 3))
                mean = np.where(cnt > 0, (v * a).sum(axis=(1, 3)) / np.maximum(cnt, 1), 0)
                oy, ox = (q >> 1) * SIZE // 2, (q & 1) * SIZE // 2
                px[oy:oy + SIZE // 2, ox:ox + SIZE // 2, 0] = np.round(mean)
                px[oy:oy + SIZE // 2, ox:ox + SIZE // 2, 1] = np.where(cnt > 0, 255, 0)
            tiles[key] = px
        changed = above
    return write_pack(tiles)


def run(local, hours, near=0, minutes=None):
    folder = local / "radar"
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    days = sorted({(now - timedelta(hours=h)).date() for h in (0, hours)})
    made = 0
    for day in days:
        nordic = Nordic(datetime(day.year, day.month, day.day))
        for when in nordic.times():
            if when < now.timestamp() - hours * 3600 or (folder / f"{when}.pack").exists():
                continue
            if minutes and datetime.fromtimestamp(when, timezone.utc).minute not in minutes:
                continue
            started = time.time()
            pack = merged(nordic, when, near)
            if pack:
                (folder / f"{when}.pack").write_bytes(pack)
                made += 1
                print(f"  {datetime.fromtimestamp(when, timezone.utc):%H:%M} {len(pack) // 1024} KB {time.time() - started:.1f} s", flush=True)
    keep = now.timestamp() - 24 * 3600
    frames = []
    for p in sorted(folder.glob("*.pack")):
        if int(p.stem) < keep:
            p.unlink()
        else:
            frames.append({"time": int(p.stem), "file": p.name})
    (folder / "index.json").write_text(json.dumps({
        "source": "MET Norway's Nordic radar composite (CC BY 4.0) over EUMETNET OPERA (CC BY 4.0): the dev map's lab",
        "zooms": [ZOOMS[0], TOP], "updated": int(now.timestamp()), "frames": frames}))
    return made


def check():
    assert map_rate(np.array([6.9]))[0] == 0 and abs(map_rate(np.array([14.5]))[0] - 0.1) < 1e-9
    assert abs(map_rate(np.array([27.5]))[0] - 1) < 1e-9 and abs(map_rate(np.array([7.0]))[0] - 0.06) < 1e-9
    assert rate_bytes(np.array([np.nan, 27.5]))[1] == 50
    tiles = {"6/1/1": np.full((SIZE, SIZE, 2), 255, np.uint8)}
    assert read_pack(write_pack(tiles))["6/1/1"].shape == (SIZE, SIZE, 2)


if __name__ == "__main__":
    check()
    args = sys.argv[1:]
    local = Path(args[args.index("--local") + 1])
    hours = float(args[args.index("--hours") + 1]) if "--hours" in args else 3
    near = int(args[args.index("--near") + 1]) if "--near" in args else 0
    minutes = {int(m) for m in args[args.index("--minutes") + 1].split(",")} if "--minutes" in args else None
    while True:
        print(f"{run(local, hours, near, minutes)} new", flush=True)
        if "--loop" not in args:
            break
        time.sleep(120)
