"""The WRF test's report (wrf-poc.yml): each step's time, the 10 m wind over the 1 km and 9 km boxes
as pictures (every 3 hours), and the wind at Tarifa hour by hour from each box; markdown on stdout
(the run's summary), pictures in <work>/report.

    python report.py work
"""
import math
import os
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from PIL import Image, ImageDraw

work = Path(sys.argv[1] if len(sys.argv) > 1 else "work")
out = work / "report"
out.mkdir(exist_ok=True)
REGION = {}
for _line in open(Path(__file__).parent / "regions" / f"{os.environ.get('REGION', 'tarifa')}.env", encoding="utf-8"):
    if "=" in _line and not _line.startswith("#"):
        _key, _value = _line.strip().split("=", 1)
        REGION[_key] = _value.strip('"')
TARIFA = (float(REGION["POINT_LAT"]), float(REGION["POINT_LON"]))
# Our wind colours (m/s): calm pale to strong purple.
STOPS = [(0, (220, 235, 240)), (3, (120, 210, 230)), (6, (80, 200, 120)), (9, (240, 220, 60)), (12, (245, 150, 40)),
         (15, (220, 50, 50)), (20, (180, 40, 160)), (30, (90, 40, 160))]


def colour(speed):
    xs = [s for s, _ in STOPS]
    return np.stack([np.interp(speed, xs, [c[i] for _, c in STOPS]) for i in range(3)], axis=-1).astype(np.uint8)


def times():
    rows = []
    path = work / "times.txt"
    if path.exists():
        for line in path.read_text().split("\n"):
            if line.strip():
                name, seconds = line.split()
                rows.append((name, int(seconds)))
    return rows


def picture(path, domain, hour, scale):
    with Dataset(path) as data:
        u, v = data["U10"][hour], data["V10"][hour]
        land = data["LANDMASK"][hour]
        stamp = "".join(c.decode() for c in data["Times"][hour])
    speed = np.hypot(u, v)[::-1]
    rgb = colour(speed)
    rgb[land[::-1] > 0.5] = (rgb[land[::-1] > 0.5] * 0.6).astype(np.uint8)
    im = Image.fromarray(rgb).resize((rgb.shape[1] * scale, rgb.shape[0] * scale), Image.NEAREST)
    draw = ImageDraw.Draw(im)
    # Arrows every few cells (the way it blows).
    step = max(rgb.shape[1] // 12, 1)
    uu, vv = np.asarray(u)[::-1], np.asarray(v)[::-1]
    for r in range(step // 2, rgb.shape[0], step):
        for c in range(step // 2, rgb.shape[1], step):
            s = math.hypot(uu[r, c], vv[r, c])
            if s < 0.5:
                continue
            x, y = (c + 0.5) * scale, (r + 0.5) * scale
            k = scale * step * 0.4
            draw.line([(x, y), (x + uu[r, c] / s * k, y - vv[r, c] / s * k)], fill=(20, 20, 30), width=2)
    draw.rectangle((0, 0, im.width, 22), fill=(20, 24, 32))
    draw.text((6, 5), f"WRF {domain} · {stamp} UTC · 10 m wind (m/s), land darker", fill="white")
    name = f"{domain}-{hour:02d}.png"
    im.save(out / name)
    return name


def at_tarifa(path):
    with Dataset(path) as data:
        lat, lon = np.asarray(data["XLAT"][0]), np.asarray(data["XLONG"][0])
        k = np.unravel_index(np.argmin((lat - TARIFA[0]) ** 2 + ((lon - TARIFA[1]) * math.cos(math.radians(TARIFA[0]))) ** 2), lat.shape)
        u, v = np.asarray(data["U10"][:, k[0], k[1]]), np.asarray(data["V10"][:, k[0], k[1]])
        stamps = ["".join(c.decode() for c in t)[8:13].replace("_", " ") for t in data["Times"][:]]
        land = float(data["LANDMASK"][0, k[0], k[1]])
    return stamps, np.hypot(u, v), (np.degrees(np.arctan2(-u, -v)) % 360), land


def main():
    print(f"## WRF test: {REGION['NAME']}, 9 → 3 → 1 km\n")
    env = dict(line.split("=", 1) for line in (work / "env").read_text().split() if "=" in line)
    print(f"GFS run {env.get('CYCLE')}, {env.get('HOURS')} hours; levels {env.get('NLEV')}, soil {env.get('NSOIL')}.\n")
    rows = times()
    if rows:
        print("| Step | Time |\n|---|---|")
        for name, seconds in rows:
            print(f"| {name} | {seconds // 60} min {seconds % 60} s |")
        wrf = dict(rows).get("wrf")
        if wrf:
            hours = int(env.get("HOURS", 12))
            per = wrf / hours
            print(f"\nwrf.exe: {per / 60:.1f} min per forecast hour on {__import__('os').cpu_count()} cores here; "
                  f"a 48-hour run would take about {per * 48 / 3600:.1f} h here.\n")
    outs = {d: sorted((work / "wrf").glob(f"wrfout_{d}_*")) for d in ("d01", "d02", "d03")}
    for domain, scale in (("d03", 6), ("d01", 6)):
        if not outs[domain]:
            continue
        with Dataset(outs[domain][0]) as data:
            count = len(data.dimensions["Time"])
        names = [picture(outs[domain][0], domain, h, scale) for h in range(0, count, 3)]
        print(f"{domain}: {count} hours, pictures {', '.join(names)}")
    print(f"\n### {REGION['POINT_NAME']} ({TARIFA[0]:.2f}, {TARIFA[1]:.2f}), 10 m wind (m/s, from °)\n")
    series = {d: at_tarifa(outs[d][0]) for d in ("d01", "d02", "d03") if outs[d]}
    others = global_models(env)
    if series:
        stamps = next(iter(series.values()))[0]
        head = " | ".join(f"WRF {d} ({'land' if series[d][3] > 0.5 else 'sea'})" for d in series)
        head += "".join(f" | {name}" for name in others)
        print(f"| Time (day hour UTC) | {head} |\n|---|" + "---|" * (len(series) + len(others)))
        for i, stamp in enumerate(stamps):
            cells = " | ".join(f"{series[d][1][i]:.1f} from {series[d][2][i]:.0f}°" if i < len(series[d][1]) else "–" for d in series)
            for values in others.values():
                good = i < len(values) and values[i][0] is not None
                cells += " | " + (f"{values[i][0]:.1f} from {values[i][1]:.0f}°" if good else "–")
            print(f"| {stamp} | {cells} |")


def global_models(env):
    """ECMWF, ICON and GFS at Tarifa for the same hours (Open-Meteo), to compare."""
    import json
    import urllib.request
    from datetime import datetime, timedelta, timezone
    start = datetime.strptime(env["CYCLE"], "%Y-%m-%dT%HZ").replace(tzinfo=timezone.utc)
    end = start + timedelta(hours=int(env.get("HOURS", 12)))
    models = {"ECMWF 9 km": "ecmwf_ifs", "ICON 13 km": "icon_global", "GFS 13 km": "gfs_global"}
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={TARIFA[0]}&longitude={TARIFA[1]}&hourly=wind_speed_10m,wind_direction_10m"
           f"&models={','.join(models.values())}&wind_speed_unit=ms&timezone=GMT"
           f"&start_hour={start:%Y-%m-%dT%H:00}&end_hour={end:%Y-%m-%dT%H:00}")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "MeteoGust WRF test"})
        with urllib.request.urlopen(request, timeout=30) as reply:
            hourly = json.load(reply)["hourly"]
    except Exception as error:
        print(f"(the global models couldn't be read: {error})")
        return {}
    return {name: list(zip(hourly[f"wind_speed_10m_{key}"], hourly[f"wind_direction_10m_{key}"])) for name, key in models.items()}


if __name__ == "__main__":
    main()
