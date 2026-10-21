"""Open-Meteo's .om files read in part over HTTP (the owner, 21.10.2026: from home the Oregon bucket gives ~17 MB/s
at most, and our jobs downloaded whole files for a few variables: ICON's world file has 118 variables, the 8 we
draw are 10 % of it). omfiles reads a file's 32 x 32 chunks one by one (14,357 requests for those 8 variables:
256 s across the Atlantic, longer than the whole file); here every read is served from aligned blocks of BLOCK
bytes, each fetched once by an HTTP range request and kept, so a variable's neighbouring chunks cost one request.
Measured from the Tower on ICON's world file (156 MB): its 8 drawn variables 72 MB in 5 requests.

omfiles' async reader kept to the first event loop it ran in and broke the next files (21.10.2026), so this is
its plain reader on a small file system object (cat_file and size, as omfiles asks): files read side by side by
the jobs' own threads, each with its own blocks.

    with RemoteOm(url) as om:
        fields = om.read_many(["wind_u_component_10m", ...])     # {name: array}
"""
import os
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import omfiles

import sources

BLOCK = 16 << 20
AGENT = "MeteoGust helper (data.meteogust.com)"


class BlockFS:
    """The two methods omfiles' reader needs, over HTTP, every read served from cached BLOCK-sized blocks."""

    def __init__(self, block=BLOCK):
        self.block = block
        self.blocks = {}
        self.sizes = {}
        self.fetched = 0
        self.requests = 0
        self.lock = threading.Lock()

    def _get(self, url, start=None, end=None):
        headers = {"User-Agent": AGENT}
        if start is not None:
            headers["Range"] = f"bytes={start}-{end - 1}"
        for attempt in range(5):
            try:
                request = urllib.request.Request(url, headers=headers, method="GET" if start is not None else "HEAD")
                with urllib.request.urlopen(request, timeout=300) as reply:
                    return reply.read() if start is not None else int(reply.headers["Content-Length"])
            except (urllib.error.URLError, OSError):
                if attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))

    def size(self, path):
        if path not in self.sizes:
            self.sizes[path] = self._get(path)
        return self.sizes[path]

    def _block(self, path, index):
        key = (path, index)
        with self.lock:
            if key in self.blocks:
                return self.blocks[key]
        start = index * self.block
        data = self._get(path, start, min(start + self.block, self.size(path)))
        with self.lock:
            self.blocks[key] = data
            self.fetched += len(data)
            self.requests += 1
        return data

    def cat_file(self, path, start=None, end=None, **_):
        size = self.size(path)
        start = 0 if start is None else (start + size if start < 0 else start)
        end = size if end is None else (end + size if end < 0 else end)
        first, last = start // self.block, (max(end, start + 1) - 1) // self.block
        joined = b"".join(self._block(path, i) for i in range(first, last + 1))
        offset = first * self.block
        return joined[start - offset:end - offset]


class RemoteOm:
    """One remote .om file, read in part."""

    def __init__(self, url, block=None):
        self.url = url
        self.fs = BlockFS(block or BLOCK)
        if block is None:
            # About a 32nd of the file, 1-16 MB: a small file's variables need small blocks to be read apart
            # (GFS 0.13°'s 37 MB files: 16 MB blocks took the whole file).
            self.fs.block = min(BLOCK, max(1 << 20, self.fs.size(url) // 32))
        self.reader = omfiles.OmFileReader.from_fsspec(self.fs, url)
        self.names = {self.reader.get_child_by_index(i).name for i in range(self.reader.num_children)}

    def read(self, name, selection=None):
        variable = self.reader.get_child_by_name(name)
        if selection is None:
            selection = tuple(slice(None) for _ in variable.shape)
        return np.asarray(variable.read_array(selection))

    def read_many(self, names):
        """Several variables whole: {name: array} (names the file lacks left out)."""
        return {n: self.read(n) for n in names if n in self.names}

    @property
    def fetched(self):
        return self.fs.fetched

    def close(self):
        try:
            self.reader.close()
        except Exception:
            pass
        self.fs.blocks = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# MARK: A drop-in for the jobs (MG_REGION=eu: at home; GitHub's runners, next to the bucket, keep downloading
# whole files, faster there).

PARTIAL = os.environ.get("MG_PARTIAL", "1" if sources.EU else "0") == "1"
# Files in a row not read before the job gives up (21.10.2026: ICON-CH2's 121 hours each failed after minutes of
# retries while a test filled the line, 2 hours stuck, and omfiles' failed reads left files open to "Too many
# open files"): the next run starts clean.
GIVE_UP = 5
_failed = [0]


class _Array:
    def __init__(self, values):
        self.values = values
        self.shape = values.shape

    def read_array(self, selection):
        return self.values[selection]


class Prefetched:
    """In place of omfiles.OmFileReader.from_path(<a downloaded file>): only the `wanted` variables of a remote
    .om file, read in part; get_child_by_name(name).read_array(selection) and close() as omfiles' reader (a
    variable the file lacks raises ValueError, as there)."""

    def __init__(self, url, wanted):
        for attempt in range(4):
            try:
                with RemoteOm(url) as om:
                    self.arrays = om.read_many(list(wanted))
                    self.fetched = om.fetched
                _failed[0] = 0
                return
            except Exception:
                if attempt == 3:
                    _failed[0] += 1
                    if _failed[0] >= GIVE_UP:
                        raise SystemExit(f"{GIVE_UP} files in a row not read (the last {url}): stopped")
                    raise
                time.sleep(10 * (attempt + 1))

    def get_child_by_name(self, name):
        if name not in self.arrays:
            raise ValueError(f"Child with name '{name}' does not exist")
        return _Array(self.arrays[name])

    def close(self):
        self.arrays = {}


def reader(path_or_prefetched):
    """omfiles' reader for a downloaded file, or the Prefetched itself."""
    if isinstance(path_or_prefetched, Prefetched):
        return path_or_prefetched
    return omfiles.OmFileReader.from_path(path_or_prefetched)


def done(path_or_prefetched):
    """The downloaded file removed (nothing to remove for a Prefetched)."""
    if isinstance(path_or_prefetched, str):
        try:
            os.unlink(path_or_prefetched)
        except OSError:
            pass


def bounded_map(fn, items, workers, ahead=None):
    """fn over items on `workers` threads, results in order, at most `ahead` waiting (a Prefetched holds its
    variables in memory: ThreadPoolExecutor.map would read every hour of a run at once; ECMWF's are ~400 MB)."""
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor
    ahead = ahead or workers * 2
    items = iter(items)
    with ThreadPoolExecutor(workers) as pool:
        waiting = deque(pool.submit(fn, item) for _, item in zip(range(ahead), items))
        while waiting:
            result = waiting.popleft().result()
            for item in items:
                waiting.append(pool.submit(fn, item))
                break
            yield result
