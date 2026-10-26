"""The gauges at home (the owner, 26.10.2026: "move the satellite clouds, the ALADIN spots file and the gauges file from
GitHub's update.yml to the Tower, shadow first, then live"): ee_gauges.py as update.yml runs it (the Estonian gauges'
pages, then gauges.py's Estonian and Latvian file), both files' history read from the published ones
(files.meteogust.com/helper/ee-gauges.json, gauges.json) instead of the repo's docs/, written in a folder of its own
(the runner's checkout is shared by every job) and uploaded as update.yml's publish_files.py does. ee_gauges.py's own
"read under 50 minutes ago" keeps it hourly.

    python gauges_home.py [--local DIR]       (--local: written to DIR, nothing uploaded)
"""
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import ee_gauges
import gauges
import publish_files

NAMES = ("ee-gauges.json", "gauges.json")


def main(args):
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    token = os.environ.get("INGEST_TOKEN")
    if not local and not token:
        raise SystemExit("INGEST_TOKEN is not set")
    folder = local or Path(tempfile.mkdtemp(prefix="gauges-"))
    folder.mkdir(parents=True, exist_ok=True)
    before = {}
    for name in NAMES:
        try:
            request = urllib.request.Request(publish_files.FILES + name, headers={"User-Agent": publish_files.AGENT})
            with urllib.request.urlopen(request, timeout=60) as reply:
                before[name] = reply.read()
            (folder / name).write_bytes(before[name])
        except Exception as error:
            print(f"published {name} not read ({error})")
    ee_gauges.OUTPUT = folder / "ee-gauges.json"
    gauges.OUTPUT = folder / "gauges.json"
    sys.argv = [sys.argv[0]]
    ee_gauges.main()
    for name in NAMES:
        path = folder / name
        after = path.read_bytes() if path.exists() else None
        if after is None or after == before.get(name):
            continue
        if local:
            print(f"written {path} ({len(after) // 1000} KB)")
        else:
            print(f"{name} ({len(after) // 1000} KB): {publish_files.upload(name, after, token)}")


if __name__ == "__main__":
    main(sys.argv[1:])
