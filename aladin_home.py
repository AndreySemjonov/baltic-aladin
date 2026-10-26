"""ALADIN's spots at home (the owner, 26.10.2026: "move the satellite clouds, the ALADIN spots file and the gauges file
from GitHub's update.yml to the Tower, shadow first, then live"): extract.py as update.yml runs it, its last run read
from the published file (files.meteogust.com/helper/aladin.json) instead of the repo's docs/aladin.json, the new one
written in a folder of its own (the runner's checkout is shared by every job) and uploaded as update.yml's
publish_files.py does. Nothing new: done in a minute.

    python aladin_home.py [--local DIR]       (--local: written to DIR/aladin.json, nothing uploaded)
"""
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import extract
import publish_files


def main(args):
    local = Path(args[args.index("--local") + 1]) if "--local" in args else None
    token = os.environ.get("INGEST_TOKEN")
    if not local and not token:
        raise SystemExit("INGEST_TOKEN is not set")
    folder = local or Path(tempfile.mkdtemp(prefix="aladin-"))
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "aladin.json"
    # The published run, for extract.py's own "already published".
    try:
        request = urllib.request.Request(publish_files.FILES + "aladin.json", headers={"User-Agent": publish_files.AGENT})
        with urllib.request.urlopen(request, timeout=60) as reply:
            before = reply.read()
        out.write_bytes(before)
    except Exception as error:
        print(f"published aladin.json not read ({error}): making the newest run")
        before = None
    extract.OUTPUT = out
    sys.argv = [sys.argv[0]]  # the newest run, only if new
    extract.main()
    after = out.read_bytes() if out.exists() else None
    if after is None or after == before:
        return
    if local:
        print(f"written {out} ({len(after) // 1000} KB)")
    else:
        print(f"aladin.json ({len(after) // 1000} KB): {publish_files.upload('aladin.json', after, token)}")


if __name__ == "__main__":
    main(sys.argv[1:])
