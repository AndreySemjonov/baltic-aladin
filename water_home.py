"""The water map at home (the owner, 22.10.2026: "Water (NEMO-EST) - lets fix this now"): nemo.py's run as update.yml
makes it on GitHub (the water map with the sea ice beside it, the spots' nemo.json, the coast points), into a folder of
its own (WATER_OUT, not the repository: the home runner's git pull mustn't meet these files), then on our file store
with publish_files.py. In shadow (--local DIR) nothing goes up: it compares with its own record (/stats/shadow/water.json)
so a run makes only what's new, as the other home jobs; live, with what the file store has.

    python water_home.py [--local DIR] [--force]
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

FILES = "https://files.meteogust.com/helper/"
SHADOW = Path("/stats/shadow/water.json")


def published_nemo_run(nemo):
    """The file store's nemo.json run when its coast points and water map are there too (as nemo.py checks), else None."""
    try:
        request = urllib.request.Request(f"{FILES}nemo.json?t={int(time.time())}", headers={"User-Agent": nemo.USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as reply:
            published = json.load(reply)
        # A file from before the gauges' shift (22.10.2026) is made again.
        run = published.get("run") if "levelShift" in published else None
    except Exception:
        return None
    return run if run and nemo.coast_published(run) else None


def main():
    args = sys.argv[1:]
    local = args[args.index("--local") + 1] if "--local" in args else None
    base = Path(local) if local else (Path("/work/water") if Path("/work").is_dir() else Path(tempfile.mkdtemp()))
    build, site = base / "build", base / "site"
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True, exist_ok=True)
    # Before nemo and water_map are imported: they read it then.
    os.environ["WATER_OUT"] = str(build)
    import nemo
    shadow = bool(local) and SHADOW.parent.parent.is_dir()
    record = {}
    if shadow:
        try:
            record = json.loads(SHADOW.read_text())
        except (OSError, ValueError):
            record = {}
        os.environ["WATER_PUBLISHED_NEMO"] = record.get("nemo") or ""
        os.environ["WATER_PUBLISHED_MAP"] = record.get("map") or ""
    else:
        os.environ["WATER_PUBLISHED_NEMO"] = published_nemo_run(nemo) or ""
        os.environ["WATER_PUBLISHED_MAP"] = nemo.published_water_run() or ""
    nemo.main()
    made = {name: (build / path).exists() for name, path in
            (("nemo", "nemo.json"), ("nemo-coast", "nemo-coast.json"), ("water", "water-site/map.json"), ("ice", "ice-site/map.json"))}
    if not any(made.values()):
        print("Water: nothing new")
        return
    shutil.rmtree(site, ignore_errors=True)
    site.mkdir(parents=True)
    for name in ("nemo", "nemo-coast"):
        if made[name]:
            shutil.copyfile(build / f"{name}.json", site / f"{name}.json")
    for name, folder in (("water", "water-site"), ("ice", "ice-site")):
        if made[name]:
            shutil.copytree(build / folder, site / name)
    print("Water: made " + ", ".join(name for name, ok in made.items() if ok), flush=True)
    if shadow or local:
        if shadow:
            if made["nemo"]:
                record["nemo"] = json.loads((build / "nemo.json").read_text()).get("run")
            if made["water"]:
                record["map"] = json.loads((build / "water-site" / "map.json").read_text()).get("run")
            SHADOW.parent.mkdir(parents=True, exist_ok=True)
            SHADOW.write_text(json.dumps(record))
        print(f"Water (local): in {site}")
        return
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    import publish_files
    for name in ("nemo", "nemo-coast"):
        path = site / f"{name}.json"
        if path.exists():
            print(f"{name}.json ({path.stat().st_size // 1000} KB): {publish_files.upload(name + '.json', path.read_bytes(), token)}", flush=True)
    for name in ("water", "ice"):
        publish_files.picture(site, name, token)


if __name__ == "__main__":
    main()
    # Out without the libraries' clean-up (h5py, the Copernicus toolbox), as the other jobs reading sea files.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
