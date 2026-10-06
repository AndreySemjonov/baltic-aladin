"""The rain map's days 3-5 from ICON-EU 7 km (DWD open data, CC BY 4.0): rain and cloud frames on
the rain map's grid for the hours after MET Nordic ends (from 54 hours after ICON-EU's run, hourly
to 78, then every 3 hours to 120), in the rain map's format, so rain_join.py can add them to it.

    <out>/map.json                  "kind": "rain", "clouds": true
    <out>/<run>/<unix time>.png     red = sqrt(precipitation mm per hour) x 50,
                                    green = how cloudy it looks x 255 (low cloud in full, middle 0.6,
                                    high 0.3, as from HARM-FI in rain_map.py)

Per ICON-EU main run (4 a day): about 40 hours x 4 fields of 1 MB.

    python rain_icon.py site/rain-icon
    python rain_icon.py --check            print the newest complete main run
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import iconeu_map as icon
import maplib
import rain_map

FRAME_STEPS = list(range(54, 79)) + list(range(81, 121, 3))
CLOUDS = ("CLCL", "CLCM", "CLCH")


def main():
    if sys.argv[1:] == ["--check"]:
        print(icon.newest_run())
        return
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/rain-icon")
    run = icon.newest_run()
    run_time = datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    (lat, lon), width, height = maplib.output_grid(rain_map.SOUTH, rain_map.NORTH, rain_map.WEST, rain_map.EAST,
                                                   rain_map.KM_PER_PIXEL)
    # Total precipitation is summed from the run's start: each frame is its difference to the step
    # before (an hour, or three after 78 hours).
    previous = {step: (step - 1 if step <= 78 else step - 3) for step in FRAME_STEPS}
    rain_steps = sorted(set(FRAME_STEPS) | set(previous.values()))

    def fetch(job):
        step, variable = job
        try:
            return step, variable, icon.download(run, step, variable)
        except Exception as error:  # a missing file leaves that hour out
            print(f"ICON-EU {variable} +{step} h: {error}")
            return step, variable, None

    jobs = [(step, "TOT_PREC") for step in rain_steps] + [(step, v) for step in FRAME_STEPS for v in CLOUDS]
    fields, sampler = {}, None
    with ThreadPoolExecutor(max_workers=8) as pool:
        for step, variable, message in pool.map(fetch, jobs):
            if message is None:
                continue
            grid, values = icon.decode(message)
            if sampler is None:
                sampler = icon.Sampler(grid, lat, lon)
            fields[(step, variable)] = np.nan_to_num(sampler(values))

    run_name = run_time.strftime("%Y%m%dT%HZ")
    folder = out / run_name
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for step in FRAME_STEPS:
        before = previous[step]
        if (step, "TOT_PREC") not in fields or (before, "TOT_PREC") not in fields:
            continue
        hours = step - before
        mm = np.clip(fields[(step, "TOT_PREC")] - fields[(before, "TOT_PREC")], 0, None) / hours
        low, mid, high = (np.clip(fields.get((step, v), np.zeros_like(mm)) / 100, 0, 1) for v in CLOUDS)
        looks = 1 - (1 - low) * (1 - 0.6 * mid) * (1 - 0.3 * high)
        red = np.clip(np.rint(np.sqrt(mm) * 50), 0, 255).astype(np.uint8)
        green = np.rint(np.clip(looks, 0, 1) * 255).astype(np.uint8)
        time = int((run_time + timedelta(hours=step)).timestamp())
        Image.fromarray(np.stack([red, green, np.zeros_like(red)], axis=-1), "RGB").save(folder / f"{time}.png", optimize=True)
        frames.append({"time": time, "file": f"{run_name}/{time}.png", "models": ["ICON-EU"]})
    manifest = {
        "model": "ICON-EU 7 km",
        "kind": "rain",
        "clouds": True,
        "source": "Deutscher Wetterdienst (DWD), ICON-EU, CC BY 4.0: https://opendata.dwd.de/",
        "run": run_time.strftime("%Y-%m-%dT%H:%MZ"),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "projection": "Web Mercator (EPSG:3857); the image spans the bounds edge to edge",
        "bounds": {"south": rain_map.SOUTH, "north": rain_map.NORTH, "west": rain_map.WEST, "east": rain_map.EAST},
        "width": width, "height": height,
        "encoding": {"red": "sqrt(precipitation mm per hour) x 50 (0 = none)",
                     "green": "how cloudy it looks x 255 (low 1, middle 0.6, high 0.3)"},
        "frames": frames,
    }
    (out / "map.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Rain (ICON-EU) run {manifest['run']}: {len(frames)} frames {width}x{height}")


if __name__ == "__main__":
    main()
