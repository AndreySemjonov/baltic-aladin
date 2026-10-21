"""Open-Meteo's .om files read in part over HTTP (the owner, 21.10.2026: from home the Oregon bucket gives ~17 MB/s
at most, and our jobs read whole files for a few variables or a few cells: ICON's world file has 118 variables,
the 8 we draw are 10 % of it). omfiles' async reader asks for each 32 x 32 chunk on its own (14,357 requests for
those 8 variables: 256 s across the Atlantic, longer than the whole file); here each request is widened to an
aligned block of BLOCK bytes, fetched once and kept, so a variable's neighbouring chunks come from one request.

    with RemoteOm(url) as om:                            # whole fields: 16 MB blocks
        fields = om.read_many(["wind_u_component_10m", ...])   # {name: array}, side by side
    with RemoteOm(url, block=CELL_BLOCK) as om:          # a few cells: small blocks
        values = om.cells_many({"temperature_2m": [(r, c), ...], ...})

Measured from the Tower 21.10.2026 on ICON's world file (156 MB, 118 variables): its 8 drawn variables 72 MB
in 5 requests with 16 MB blocks.
"""
import asyncio
import os
import threading
import time

import numpy as np
import omfiles
try:
    from fsspec.implementations.http import HTTPFileSystem
except ImportError:  # GitHub's runners don't install it: whole files there, as before.
    HTTPFileSystem = object

import sources

BLOCK = 16 << 20
CELL_BLOCK = 64 << 10


class _BlockFS(HTTPFileSystem):
    """fsspec's HTTP file system, each read served from BLOCK-sized cached blocks."""

    def __init__(self, block=BLOCK, **kw):
        # Its own instance (fsspec keeps one per arguments, and with it every block it ever read).
        super().__init__(asynchronous=True, skip_instance_cache=True, **kw)
        self.block = block
        self.blocks = {}
        self.waiting = {}
        self.fetched = 0
        self.requests = 0
        self.size_cache = {}

    async def _block(self, path, index, size):
        key = (path, index)
        if key in self.blocks:
            return self.blocks[key]
        if key in self.waiting:
            return await self.waiting[key]
        future = asyncio.get_running_loop().create_future()
        self.waiting[key] = future
        try:
            start = index * self.block
            data = await super()._cat_file(path, start=start, end=min(start + self.block, size))
            self.fetched += len(data)
            self.requests += 1
            self.blocks[key] = data
            future.set_result(data)
            return data
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            del self.waiting[key]

    async def _cat_file(self, path, start=None, end=None, **kw):
        size = await self._size(path)
        start = 0 if start is None else (start + size if start < 0 else start)
        end = size if end is None else (end + size if end < 0 else end)
        first, last = start // self.block, (max(end, start + 1) - 1) // self.block
        parts = await asyncio.gather(*(self._block(path, i, size) for i in range(first, last + 1)))
        joined = b"".join(parts)
        offset = first * self.block
        return joined[start - offset:end - offset]

    async def _size(self, path):
        if path not in self.size_cache:
            self.size_cache[path] = await super()._size(path)
        return self.size_cache[path]


class RemoteOm:
    """One remote .om file, read in part (its own event loop on a thread, so plain code can call it)."""

    def __init__(self, url, block=BLOCK):
        self.url = url
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.fs = self._run(self._make_fs(block))
        self.reader = self._run(self._open())
        self.names = {self._run(self._child_name(i)) for i in range(self.reader.num_children)}

    async def _make_fs(self, block):
        return _BlockFS(block=block)

    def _run(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()

    async def _open(self):
        made = omfiles.OmFileReaderAsync.from_fsspec(self.fs, self.url)
        return await made if asyncio.iscoroutine(made) or isinstance(made, asyncio.Future) else made

    async def _child_name(self, i):
        child = self.reader.get_child_by_index(i)
        child = await child if asyncio.iscoroutine(child) else child
        return child.name

    async def _variable(self, name):
        child = self.reader.get_child_by_name(name)
        return await child if asyncio.iscoroutine(child) else child

    async def _read(self, name, selection):
        variable = await self._variable(name)
        if selection is None:
            selection = tuple(slice(None) for _ in variable.shape)
        data = variable.read_array(selection)
        data = await data if asyncio.iscoroutine(data) or isinstance(data, asyncio.Future) else data
        return np.asarray(data)

    def shape(self, name):
        return tuple(self._run(self._variable(name)).shape)

    def read(self, name, selection=None):
        """A variable whole (or a selection of it)."""
        return self._run(self._read(name, selection))

    def read_many(self, names):
        """Several variables whole, read side by side: {name: array} (names the file lacks left out)."""
        names = [n for n in names if n in self.names]

        async def every():
            return await asyncio.gather(*(self._read(n, None) for n in names))
        return dict(zip(names, self._run(every())))

    def cells_many(self, wanted):
        """{name: [(row, col), ...]} -> {name: [values]}, every read side by side."""
        wanted = {n: p for n, p in wanted.items() if n in self.names}
        shapes = {n: self.shape(n) for n in wanted}

        async def one(name, r, c):
            selection = (slice(r, r + 1), slice(c, c + 1)) if len(shapes[name]) == 2 else (slice(0, 1), slice(r, r + 1), slice(c, c + 1))
            return (await self._read(name, selection)).ravel()[0]

        async def every():
            return await asyncio.gather(*(one(n, r, c) for n, places in wanted.items() for r, c in places))
        flat = iter(self._run(every()))
        return {n: [next(flat) for _ in places] for n, places in wanted.items()}

    def cells(self, name, places):
        """A few cells of a 2-D (or leading-1 3-D) variable: [(row, col), ...] -> values."""
        shape = self.shape(name)

        async def one(r, c):
            selection = (slice(r, r + 1), slice(c, c + 1)) if len(shape) == 2 else (slice(0, 1), slice(r, r + 1), slice(c, c + 1))
            return (await self._read(name, selection)).ravel()[0]
        async def every():
            return await asyncio.gather(*(one(r, c) for r, c in places))
        return self._run(every())

    @property
    def fetched(self):
        return self.fs.fetched

    async def _close_session(self):
        session = await self.fs.set_session()
        await session.close()

    def close(self):
        try:
            self._run(self._close_session())
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# MARK: A drop-in for the jobs (MG_REGION=eu: at home; GitHub's runners, next to the bucket, keep downloading
# whole files, faster there).

PARTIAL = os.environ.get("MG_PARTIAL", "1" if sources.EU else "0") == "1" and HTTPFileSystem is not object


class _Array:
    def __init__(self, values):
        self.values = values
        self.shape = values.shape

    def read_array(self, selection):
        return self.values[selection]


class Prefetched:
    """In place of omfiles.OmFileReader.from_path(<a downloaded file>): only the `wanted` variables of a remote
    .om file, read in part and side by side; get_child_by_name(name).read_array(selection) and close() as
    omfiles' reader (a variable the file lacks raises ValueError, as there)."""

    def __init__(self, url, wanted):
        for attempt in range(4):
            try:
                with RemoteOm(url) as om:
                    self.arrays = om.read_many(list(wanted))
                    self.fetched = om.fetched
                return
            except Exception:
                if attempt == 3:
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
