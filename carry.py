"""Copy a published map (map.json and its frames) from the live site into a new site build,
so a deploy that doesn't rebuild that map keeps it (GitHub Pages replaces the whole site).

    python carry.py site/icon-eu icon-eu
"""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SITE = "https://andreysemjonov.github.io/baltic-aladin/"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as reply:
        return reply.read()


def main():
    out, path = Path(sys.argv[1]), sys.argv[2].strip("/")
    manifest = get(f"{SITE}{path}/map.json")
    files = [frame["file"] for frame in json.loads(manifest)["frames"]]

    def copy(name):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(get(f"{SITE}{path}/{name}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(copy, files))
    (out / "map.json").write_bytes(manifest)
    print(f"carried {path}: {len(files)} frames")


if __name__ == "__main__":
    main()
