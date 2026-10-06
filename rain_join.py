"""Joins the rain map's days 3-5 (site/rain-icon, ICON-EU) onto its MET Nordic hours (site/rain):
ICON-EU's frames after MET Nordic's last hour are copied into site/rain and added to its map.json
(marked "models": ["ICON-EU"]). Frames joined before are replaced, so running it again, or on a
carried rain map, gives the same result.

    python rain_join.py site
"""
import json
import shutil
import sys
from pathlib import Path


def main():
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    rain_path, icon_path = site / "rain" / "map.json", site / "rain-icon" / "map.json"
    if not rain_path.exists() or not icon_path.exists():
        print("rain join: a map is missing, nothing joined")
        return
    rain, icon = json.loads(rain_path.read_text(encoding="utf-8")), json.loads(icon_path.read_text(encoding="utf-8"))
    if (rain["width"], rain["height"], rain["bounds"]) != (icon["width"], icon["height"], icon["bounds"]):
        print("rain join: the grids differ, nothing joined")
        return
    nordic = [f for f in rain["frames"] if f.get("models") != ["ICON-EU"]]
    last = max((f["time"] for f in nordic), default=0)
    added = []
    for frame in icon["frames"]:
        if frame["time"] <= last:
            continue
        source, target = site / "rain-icon" / frame["file"], site / "rain" / frame["file"]
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        added.append(frame)
    rain["frames"] = nordic + added
    rain_path.write_text(json.dumps(rain, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"rain join: {len(nordic)} MET Nordic hours + {len(added)} ICON-EU frames")


if __name__ == "__main__":
    main()
