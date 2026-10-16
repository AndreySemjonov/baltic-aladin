"""The world's seas as one tile set (the owner, 16.10.2026: "is it possible to have complete final
world current?"): the Copernicus global ocean forecast (GLO PHY, about 9 km, hourly at the surface)
as world tiles under Europe's detailed set (tiles/sea, water_composite.py), in the same pixels:

    red   = the level: 128 + 4.8 x sign x sqrt(|cm|), 1-255 (0 = no sea), against its mean over
            the first day at each place (the model's own reference taken off; no tide in it)
    green = the water temperature: (degrees C + 4) x 7
    blue  = the current's east part: 128 + m/s x 42 (1-255)
    alpha = its north part, the same

Every hour from 12 hours back to 3 days on, then every 3 hours to 7 days, zooms 0-4 of 256 pixels
(zoom 4 about 10 km a pixel at the equator, the model's own), round the world (tiles/sea-world). The global model has no tide: its current is the ocean's
own and the wind's; Europe's set (with the tide where its models have it) goes over it on the map.

Free data, but Copernicus needs our account (COPERNICUSMARINE_SERVICE_USERNAME and _PASSWORD,
GitHub secrets). Source: E.U. Copernicus Marine Service Information.

    python sea_world.py [--local DIR] [--hours N]
"""
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

import tiler
import world_tiles
from water_composite import GLO, NEAR, check, nearest, open_dataset, sea_rgba, stamps_of
from scipy import ndimage

from waves_tiles import regular_grid

ZOOMS = range(0, 5)
STEPS = list(range(-12, 72)) + list(range(72, 7 * 24 + 1, 3))
REACH = 3  # cells the sea's values reach onto the land (so the coast's pixels blend with sea)


class Filler:
    """The land within REACH cells of the sea, each from its nearest sea cell: worked out once (the
    land is the same every hour), used for every field and hour."""

    def __init__(self, missing):
        distance, (self.rows, self.cols) = ndimage.distance_transform_edt(missing, return_indices=True)
        self.missing = missing
        self.near = distance <= REACH

    def __call__(self, raw):
        out = raw[self.rows, self.cols]
        return np.where(self.near, out, np.nan).astype(np.float32)
VARIABLES = {"level": "zos", "temp": "thetao", "u": "uo", "v": "vo"}
CREDIT = "E.U. Copernicus Marine Service Information (GLO physics forecast)"


def grids(ds, index, offset, fill):
    """The hour's fields as (place, sample, values), the land filled 3 cells out, round the world."""
    lats, lons = ds["latitude"].values.astype(np.float64), ds["longitude"].values.astype(np.float64)
    place, sample = regular_grid(lats[0], lons[0], lats[1] - lats[0], lons[1] - lons[0], len(lats), len(lons), True)
    out = {}
    raw_level = raw_temp = None
    for field, variable in VARIABLES.items():
        raw = ds[variable].values[index].astype(np.float32)
        if field == "level" and offset is not None:
            raw = raw - offset
        if field == "level":
            raw_level = raw
        if field == "temp":
            raw_temp = raw
        # A cell the land mask calls sea but this field lacks (rare): its nearest sea cell's value too.
        out[field] = fill(np.where(np.isfinite(raw), raw, 0) if field != "level" else raw)
    return place, sample, out, check(raw_level, raw_temp)


def pack(place, sample, fields, keys):
    tiles = {}
    for key in keys:
        lon, lat = tiler.tile_lonlat(*key)
        col, row, inside = place(lon, lat)
        level = sample(fields["level"], col, row)
        sea = (inside > 0) & np.isfinite(level)
        if not np.any(sea):
            continue
        values = [np.nan_to_num(sample(fields[f], col, row)) for f in ("temp", "u", "v")]
        rgba = sea_rgba(np.nan_to_num(level), *values)
        rgba[~sea] = 0
        tiles[key] = world_tiles.png(rgba.astype(np.uint8), "RGBA")
    return world_tiles.encode(tiles, {z: tiler.SIZE for z in ZOOMS})


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    limit = int(args[args.index("--hours") + 1]) if "--hours" in args else None
    started = time.time()
    run_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    targets = [int((run_time + timedelta(hours=s)).timestamp()) for s in (STEPS[:limit] if limit else STEPS)]
    ds = open_dataset(GLO, list(VARIABLES.values()), span=(min(targets), max(targets)))
    stamps = stamps_of(ds)
    wanted = [(t, nearest(stamps, t)) for t in targets]
    wanted = [(t, k) for t, k in wanted if k is not None]
    print(f"GLO: {len(wanted)} of {len(targets)} hours, {time.time() - started:.0f} s", flush=True)
    if not wanted:
        raise SystemExit("the global model has none of the hours")
    # The level's own reference off: its mean over the first day (every 3 hours) at each place.
    first = [k for t, k in wanted if t < wanted[0][0] + 24 * 3600]
    offset = np.nanmean(ds["zos"].isel(time=first).load().values.astype(np.float32), axis=0)
    print(f"level mean from {len(first)} hours, {time.time() - started:.0f} s", flush=True)
    keys = [(z, x, y) for z in ZOOMS for x in range(1 << z) for y in range(1 << z)]
    out = tiler.Run("sea-world", run_time, ZOOMS, "Copernicus global ocean", CREDIT, True, local)
    fill = Filler(~np.isfinite(offset))
    # A block of hours read at once (16.10.2026: hour by hour took 100 s each, Copernicus's chunks
    # span many hours and each was read again for every hour); the hours' places in the block.
    BLOCK = 12
    for start in range(0, len(wanted), BLOCK):
        block = wanted[start:start + BLOCK]
        indices = sorted(set(k for _, k in block))
        loaded = ds.isel(time=indices).load()
        for t, k in block:
            place, sample, fields, reason = grids(loaded, indices.index(k), offset, fill)
            if not out.checked("GLO", t, reason):
                continue
            out.put(t, pack(place, sample, fields, keys))
            print(f"  {datetime.fromtimestamp(t, timezone.utc):%d.%m %H}Z: {len(out.frames)} hours, {out.bytes / 1e6:.0f} MB, "
                  f"{time.time() - started:.0f} s", flush=True)
        del loaded
    if not out.frames:
        raise SystemExit("no hours made")
    out.finish(encoding="level 128 + 4.8 sign sqrt|cm| (0 no sea; against the first day's mean), (C + 4) x 7, "
                        "current east and north 128 + m/s x 42")


if __name__ == "__main__":
    main()
