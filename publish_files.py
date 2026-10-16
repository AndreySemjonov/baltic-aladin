"""The phones' small data files on our file store too (the owner, 16.10.2026: off GitHub Pages,
whose traffic is capped at about 100 GB a month, before the public release): the ALADIN spots
(aladin.json), the Latvian and Estonian gauges and the Baltic's water level (nemo.json, its coast
points), from the site folder update.yml publishes, to files.meteogust.com/helper/<name>.json
through station-history (INGEST_TOKEN). Pages keeps them too while older builds read them there.

    python publish_files.py site
"""
import os
import sys
import time
import urllib.request
from pathlib import Path

NAMES = ("aladin", "gauges", "ee-gauges", "nemo", "nemo-coast")
UPLOAD = "https://data.meteogust.com/tiles/upload/helper/"


def upload(name, data, token):
    for attempt in range(3):
        try:
            request = urllib.request.Request(UPLOAD + name, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token,
                                                      "User-Agent": "MeteoGust helper files (data.meteogust.com)"})
            with urllib.request.urlopen(request, timeout=60) as reply:
                return reply.read().decode().strip()
        except Exception as error:
            if attempt == 2:
                return f"failed: {error}"
            time.sleep(5 * (attempt + 1))


def main():
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        sys.exit("INGEST_TOKEN is not set")
    for name in NAMES:
        path = folder / f"{name}.json"
        if path.exists():
            print(f"{name}.json ({path.stat().st_size // 1000} KB): {upload(name + '.json', path.read_bytes(), token)}", flush=True)


if __name__ == "__main__":
    main()
