"""Our own map packs kept on the home runner's disk (the owner, 23.10.2026: "we shouldn't download duplicates ... in
ideal scenario we download only changes"; 66% of the Tower's downloads were our own maps read back by the jobs that build
on them: the Mix, the world mix, the weather mixes, the places' files, the spot points).

A pack under files.meteogust.com/tiles/ never changes under its name (each run has folders and names of its own; they
are kept a year in caches), so each is downloaded once and read from the disk after, or never downloaded when this
machine made it (world_tiles.upload stores it as it goes up). A read touches the file; the runner trims the folder to its
cap, least recently used first (runner/scheduler.py trim_cache).

MG_CACHE: the folder (the runner mounts D:\\meteogust\\cache at /cache); without it (GitHub, a person's test) nothing
changes. Each job's hits and misses go to /stats/_cache/<job>.json (the hit rate says whether the cap is big enough).
"""
import json
import os
import re
import threading
import time

ROOT = os.environ.get("MG_CACHE")
# Only the immutable packs: tiles/<set>/<run>/<file>.pack, and the static ones (coast, terrain) under tiles/.
PACK = re.compile(r"^https://files\.meteogust\.com/tiles/([a-z0-9][A-Za-z0-9/._-]*\.pack)$")
_lock = threading.Lock()
_count = {"hits": 0, "misses": 0, "stored": 0, "hit_mb": 0.0, "miss_mb": 0.0}
_last_save = [0.0]


def _path(url):
    if not ROOT or "?" in url:
        return None
    m = PACK.match(url)
    if not m or ".." in m.group(1):
        return None
    return os.path.join(ROOT, "tiles", m.group(1))


def _note(kind, size):
    with _lock:
        _count[kind] += 1
        if kind == "hits":
            _count["hit_mb"] += size / 1e6
        elif kind == "misses":
            _count["miss_mb"] += size / 1e6
        if time.time() - _last_save[0] < 30:
            return
        _last_save[0] = time.time()
        body = dict(_count, t=round(time.time()))
    job = os.environ.get("MG_JOB")
    if job and os.path.isdir("/stats"):
        try:
            os.makedirs("/stats/_cache", exist_ok=True)
            with open(f"/stats/_cache/{job}.json", "w") as f:
                json.dump({k: round(v, 1) if isinstance(v, float) else v for k, v in body.items()}, f)
        except OSError:
            pass


def read(url):
    """The whole pack from the disk (touched), or None."""
    path = _path(url)
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
        os.utime(path)
    except OSError:
        return None
    _note("hits", len(data))
    return data


def read_range(url, start, length):
    """A part of a pack from the disk, or None when it isn't there."""
    path = _path(url)
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(length)
        os.utime(path)
    except OSError:
        return None
    # Past the end: what's there, as an HTTP range does (a pack here is always whole: written, then renamed).
    if not data:
        return None
    _note("hits", len(data))
    return data


def store(url, data, downloaded=True):
    """A pack onto the disk (a file of its own first, then renamed: readers never see half a pack)."""
    path = _path(url)
    if not path or not data:
        return
    if downloaded:
        _note("misses", len(data))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        with _lock:
            _count["stored"] += 1
    except OSError:
        pass


def enabled():
    return bool(ROOT)
