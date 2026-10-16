"""The phones' data on our file store (the owner, 16.10.2026: off GitHub Pages, whose traffic is
capped at about 100 GB a month, "a unified solution all over the app"): the ALADIN spots
(aladin.json), the Latvian and Estonian gauges, the Baltic's water level (nemo.json, its coast
points), and the water and ice pictures (water/, ice/: map.json, land.png and the run's frames),
from the site folder update.yml publishes, to files.meteogust.com/helper/ through station-history
(INGEST_TOKEN). A picture's frames go up only when its run is new (its map.json differs from the
published one); its map.json last, so phones never see a list before its frames. Pages keeps them
too while older builds read them there.

    python publish_files.py site
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

NAMES = ("aladin", "gauges", "ee-gauges", "nemo", "nemo-coast")
PICTURES = ("water", "ice")
UPLOAD = "https://data.meteogust.com/tiles/upload/helper/"
FILES = "https://files.meteogust.com/helper/"
AGENT = "MeteoGust helper files (data.meteogust.com)"


def upload(name, data, token):
    for attempt in range(3):
        try:
            request = urllib.request.Request(UPLOAD + name, data=data, method="PUT",
                                             headers={"Authorization": "Bearer " + token, "User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=60) as reply:
                return reply.read().decode().strip()
        except Exception as error:
            if attempt == 2:
                return f"failed: {error}"
            time.sleep(5 * (attempt + 1))


def published(name):
    try:
        request = urllib.request.Request(FILES + name + f"?t={int(time.time())}", headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=30) as reply:
            return reply.read()
    except Exception:
        return None


def picture(folder, name, token):
    """A picture's new run: its frames and land, then its map.json."""
    listing = folder / name / "map.json"
    if not listing.exists():
        return
    data = listing.read_bytes()
    if published(f"{name}/map.json") == data:
        print(f"{name}: published already", flush=True)
        return
    frames = json.loads(data).get("frames", [])
    failed = 0
    for frame in frames:
        path = folder / name / frame["file"]
        if path.exists() and not upload(f"{name}/{frame['file']}", path.read_bytes(), token).startswith("ok"):
            failed += 1
    land = folder / name / "land.png"
    if land.exists():
        upload(f"{name}/land.png", land.read_bytes(), token)
    if failed:
        print(f"{name}: {failed} frames failed, its list left as it was", flush=True)
        return
    print(f"{name}: {len(frames)} frames, list {upload(f'{name}/map.json', data, token)}", flush=True)


def main():
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        sys.exit("INGEST_TOKEN is not set")
    for name in NAMES:
        path = folder / f"{name}.json"
        if path.exists():
            print(f"{name}.json ({path.stat().st_size // 1000} KB): {upload(name + '.json', path.read_bytes(), token)}", flush=True)
    for name in PICTURES:
        picture(folder, name, token)


if __name__ == "__main__":
    main()
