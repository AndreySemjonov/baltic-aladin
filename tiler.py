"""The shared tiler (docs/TILES_PLAN.md in the app's repository, phase 1; the owner, 12.10.2026:
every map layer as tiles, at its source's full area). A source gives its values on its own grid
and how much it counts at a place (`place(lon, lat)` -> column, row, weight: 1 inside, fading to 0
over its area's edge). The tiler finds where every tile's pixels lie on that grid once, then makes
each hour's tiles from the values (alpha: the weight; tiles where the source has nothing are left
out), packs them (MGT1, as world_tiles) and publishes the run through station-history: its packs,
then its index. A run's folder is never reused: the same run published again gets a suffix (the
packs are kept a year in caches).

    import tiler
    keys = tiler.tiles_in_box((south, north, west, east), range(3, 6))
    placement = tiler.Placement(keys, place)
    run = tiler.Run("icon-eu", run_time, zooms=range(3, 6), model="ICON-EU 7 km", credit="...")
    if run.wanted:
        run.put(time, tiler.pack(placement, sample, lambda at: tiler.wind_rgb(at(u), at(v), at(g))))
        run.finish()
"""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import quality
import temp_map
import world_tiles

SIZE = 256


def tile_lonlat(z, x, y, size=SIZE):
    """Longitude and latitude of each pixel centre of tile z/x/y (row 0 north)."""
    n = 2 ** z
    xs = (x + (np.arange(size) + 0.5) / size) / n
    ys = (y + (np.arange(size) + 0.5) / size) / n
    lon = xs * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * ys))))
    lat, lon = np.meshgrid(lat, lon, indexing="ij")
    return lon, lat


def tiles_in_box(box, zooms):
    """The tiles (z, x, y) touching a box (south, north, west, east) at each zoom."""
    south, north, west, east = box
    keys = []
    for z in zooms:
        n = 2 ** z

        def ty(lat):
            return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
        x0, x1 = int((west + 180) / 360 * n), int(min((east + 180) / 360 * n, n - 1e-9))
        y0, y1 = int(ty(min(north, 85.05))), int(min(ty(max(south, -85.05)), n - 1e-9))
        keys += [(z, x, y) for x in range(max(x0, 0), min(x1, n - 1) + 1) for y in range(max(y0, 0), min(y1, n - 1) + 1)]
    return keys


def edge_weight(lon, lat, box, fade_km):
    """1 inside a box, falling to 0 over its last `fade_km` (about 111 km a degree)."""
    south, north, west, east = box
    km = np.minimum.reduce([(lat - south) * 111, (north - lat) * 111,
                            (lon - west) * 111 * np.cos(np.radians(lat)), (east - lon) * 111 * np.cos(np.radians(lat))])
    return np.clip(km / fade_km, 0, 1)


class Placement:
    """Where every tile's pixels lie on a source's grid, and how much it counts there (tiles
    where it counts nowhere are dropped)."""

    def __init__(self, keys, place):
        self.zooms = sorted({key[0] for key in keys})
        self.parts = {}
        for key in keys:
            lon, lat = tile_lonlat(*key)
            col, row, weight = place(lon, lat)
            if np.any(weight > 0):
                self.parts[key] = (col, row, weight)


# MARK: Encodings (as world_tiles and the Baltic maps: the app reads them the same way)

def wind_rgb(east, north, gust):
    """East and north m/s x 4 + 128, the gust m/s x 5 (at least the wind)."""
    east, north = np.nan_to_num(east), np.nan_to_num(north)
    gust = np.maximum(np.nan_to_num(gust), np.hypot(east, north))
    return np.stack([np.clip(np.rint(east * 4 + 128), 0, 255), np.clip(np.rint(north * 4 + 128), 0, 255),
                     np.clip(np.rint(gust * 5), 0, 255)], axis=-1)


def steer_rgb(east, north):
    """The wind aloft that carries the showers (700 hPa): east and north m/s x 2 + 128 (to 64 m/s)."""
    east, north = np.nan_to_num(east), np.nan_to_num(north)
    return np.stack([np.clip(np.rint(east * 2 + 128), 0, 255), np.clip(np.rint(north * 2 + 128), 0, 255),
                     np.zeros(east.shape)], axis=-1)


def waves_rgb(height, period, direction):
    """The waves (docs/WAVES_PLAN.md in the app's repository): the height m x 20 + 1 (to 12.7 m;
    0 = no data), the period s x 10, the direction it comes from degrees x 256 / 360."""
    height = np.asarray(height, dtype=np.float32)
    known = np.isfinite(height)
    return np.stack([np.where(known, np.clip(np.rint(np.nan_to_num(height) * 20) + 1, 1, 255), 0),
                     np.clip(np.rint(np.nan_to_num(period) * 10), 0, 255),
                     np.mod(np.rint(np.nan_to_num(direction) * 256 / 360), 256)], axis=-1)


def temp_rgb(air, feels, hpa):
    """Air and feels-like (temp_map.encode: (C + 40) x 3 + 1), sea-level pressure (hPa - 940) x 2;
    0 = unknown."""
    hpa = np.asarray(hpa, dtype=np.float32)
    return np.stack([temp_map.encode(air), temp_map.encode(feels),
                     np.where(np.isfinite(hpa), np.clip(np.rint((hpa - 940) * 2), 1, 255), 0)], axis=-1)


def cloud_rgb(low, mid, high):
    """The clouds' cover in layers (0-1 each) x 255: low (under about 2 km), mid, high."""
    return np.stack([np.clip(np.rint(np.nan_to_num(v) * 255), 0, 255) for v in (low, mid, high)], axis=-1)


def rain_rgb(mm, cloud):
    """sqrt(mm an hour) x 50, the cloud cover (0-1) x 255."""
    mm = np.maximum(np.nan_to_num(mm), 0)
    cloud = np.clip(np.nan_to_num(cloud), 0, 1)
    return np.stack([np.clip(np.rint(np.sqrt(mm) * 50), 0, 255), np.clip(np.rint(cloud * 255), 0, 255),
                     np.zeros(mm.shape)], axis=-1)


def pack(placement, sample, encode, valid=None):
    """One hour's pack: each tile's RGB from `encode(at)` (`at(grid)`: the grid sampled at the
    tile's pixels) and the weight as alpha (0 where `valid(at)` says no)."""
    tiles = {}
    for key, (col, row, weight) in placement.parts.items():
        def at(grid):
            return sample(grid, col, row)
        rgb = encode(at)
        w = weight if valid is None else np.where(valid(at), weight, 0)
        alpha = np.rint(np.clip(np.nan_to_num(w), 0, 1) * 255)
        if alpha.max() == 0:
            continue
        tiles[key] = world_tiles.png(np.concatenate([rgb, alpha[..., None]], axis=-1).astype(np.uint8), "RGBA")
    return world_tiles.encode(tiles, {z: SIZE for z in placement.zooms})


# MARK: Publishing

class Run:
    """One run of a tile set: its packs (tiles/<set>/<folder>/<time>[-temp|-rain].pack) and its
    index. `wanted` is False when the run is published already (unless `force`). With `local`
    (a folder), written there instead of uploaded."""

    def __init__(self, name, run_time, zooms, model, credit, force=False, local=None):
        self.name, self.model, self.credit, self.local = name, model, credit, Path(local) if local else None
        self.run_time, self.zooms = run_time, list(zooms)
        self.folder = run_time.strftime("%Y%m%dT%HZ")
        published = None if self.local else world_tiles.published(name)
        self.wanted = force or published != self.folder
        # The same run again: a folder of its own.
        if published and published.split("-")[0] == self.folder:
            self.folder += "-" + format(int(time.time()) % 36 ** 6, "x")
        self.frames = {}
        self.bytes = 0
        self.report = quality.Report(name)
        # The published run's far hours to keep (world_tiles.far_frames; not for a test or a forced run).
        self.far = world_tiles.far_frames(world_tiles.published_index(name)) if self.wanted and not self.local and not force else {}
        self.far_kept = 0

    def kept_far(self, unix_time):
        """True when this hour keeps the published run's packs (past the first day, made under 3 hours ago): its
        frame goes in the index as it was, nothing made or uploaded."""
        frame = self.far.get(int(unix_time))
        if frame is None:
            return False
        self.frames[int(unix_time)] = frame
        self.far_kept += 1
        return True

    def checked(self, model, unix_time, reason):
        """An hour's check (`reason` from quality, None: fine): False leaves the hour out."""
        self.report.count()
        if reason:
            self.report.fail(model, unix_time, reason)
            return False
        return True

    def publish_check(self, published):
        """The checks' results (tiles/<set>/check.json, for the admin panel; refused before
        station-history knows the file: the run goes on)."""
        body = self.report.body(self.run_time.strftime("%Y-%m-%dT%H:%MZ"), published)
        try:
            if self.local:
                (self.local / "check.json").write_bytes(body)
            else:
                world_tiles.upload(f"{self.name}/check.json", body)
        except Exception as error:
            print(f"{self.name}: check.json not sent ({error})", flush=True)

    def put(self, unix_time, data, kind=None):
        """One hour's pack (kind: None the wind, "temp", "rain" or "cloud")."""
        file = f"{self.folder}/{unix_time}{'-' + kind if kind else ''}.pack"
        if self.local:
            (self.local / self.folder).mkdir(parents=True, exist_ok=True)
            (self.local / file).write_bytes(data)
        else:
            world_tiles.upload(f"{self.name}/{file}", data)
        frame = self.frames.setdefault(int(unix_time), {"time": int(unix_time)})
        frame["made"] = round(time.time())
        if kind:
            frame[kind] = file
            frame.setdefault("file", file)
        else:
            frame["file"] = file
        self.bytes += len(data)
        return file

    def finish(self, tail=False, **extra):
        """The index, after the packs (station-history records the run for the admin panel); not
        when over a fifth of the hours failed their checks (the run before stays). `tail`: a short run
        (ICON-EU's 03/09/15/21 UTC), its hours followed by the published run's later ones (the owner,
        21.10.2026; station-history keeps every folder an index points to)."""
        failed = len(self.report.failures)
        if self.report.checked and failed > 0.2 * self.report.checked:
            self.publish_check(False)
            raise SystemExit(f"{self.name}: {failed} of {self.report.checked} hours failed their checks; not published")
        index = {"model": self.model, "credit": self.credit, "run": self.run_time.strftime("%Y-%m-%dT%H:%MZ"),
                 "runName": self.folder, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                 "zooms": [min(self.zooms), max(self.zooms)], "land": None,
                 "frames": [self.frames[t] for t in sorted(self.frames)], **extra}
        if tail and index["frames"] and not self.local:
            previous = world_tiles.published_index(self.name)
            last = index["frames"][-1]["time"]
            later = [f for f in sorted((previous or {}).get("frames") or [], key=lambda f: f["time"]) if f["time"] > last]
            index["frames"] += later
            print(f"{self.name} {self.folder}: {len(later)} hours of the run before after its own", flush=True)
        body = json.dumps(index, separators=(",", ":")).encode()
        if self.local:
            # (Every hour kept from the published run: no pack made the folder.)
            self.local.mkdir(parents=True, exist_ok=True)
            (self.local / "index.json").write_bytes(body)
        else:
            world_tiles.upload(f"{self.name}/index.json", body)
        self.publish_check(True)
        print(f"{self.name} {self.folder}: {len(self.frames)} hours, {self.bytes / 1e6:.0f} MB", flush=True)
