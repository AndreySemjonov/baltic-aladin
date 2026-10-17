"""A WRF run's map tiles made on our own computer (local.sh without INGEST_TOKEN: tiles.py --local),
uploaded from here (wrf-publish.yml, with the upload secret): the packs first, then check.json, the
index last (as tiler.Run does), so phones never see an index before its packs. Standard library
only (as world_tiles.upload).

    python wrf_poc/publish_tiles.py DIR wrf-tarifa
"""
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UPLOAD = "https://data.meteogust.com/tiles/upload/"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"


def upload(path, data):
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    for attempt in range(4):
        try:
            request = urllib.request.Request(UPLOAD + path, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=300) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 3:
                raise
        except OSError:
            if attempt == 3:
                raise
        time.sleep(10 * (attempt + 1))


def main():
    folder, name = Path(sys.argv[1]), sys.argv[2]
    if not name.startswith("wrf-"):
        raise SystemExit("Only WRF's sets")
    packs = sorted(folder.rglob("*.pack"))
    if not packs or not (folder / "index.json").exists():
        raise SystemExit("No packs or no index")
    for path in packs:
        upload(f"{name}/{path.relative_to(folder).as_posix()}", path.read_bytes())
    for last in ("check.json", "index.json"):
        if (folder / last).exists():
            upload(f"{name}/{last}", (folder / last).read_bytes())
    print(f"{name}: {len(packs)} packs and the index uploaded", flush=True)


if __name__ == "__main__":
    main()
