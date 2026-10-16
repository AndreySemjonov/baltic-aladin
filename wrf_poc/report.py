"""The WRF test's report (wrf-poc.yml): each step's time, the 10 m wind over the 1 km and 9 km boxes
as pictures (every 3 hours), and the wind at Tarifa hour by hour from each box; markdown on stdout
(the run's summary), pictures in <work>/report.

    python report.py work
"""
import math
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from PIL import Image, ImageDraw

work = Path(sys.argv[1] if len(sys.argv) > 1 else "work")
out = work / "report"
out.mkdir(exist_ok=True)
TARIFA = (36.013, -5.606)
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
        k = np.unravel_index(np.argmin((lat - TARIFA[0]) ** 2 + ((lon - TARIFA[1]) * math.cos(math.radians(36))) ** 2), lat.shape)
        u, v = np.asarray(data["U10"][:, k[0], k[1]]), np.asarray(data["V10"][:, k[0], k[1]])
        stamps = ["".join(c.decode() for c in t)[8:13].replace("_", " ") for t in data["Times"][:]]
        land = float(data["LANDMASK"][0, k[0], k[1]])
    return stamps, np.hypot(u, v), (np.degrees(np.arctan2(-u, -v)) % 360), land


def main():
    print("## WRF test: Tarifa, 9 → 3 → 1 km\n")
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
    print("\n### Tarifa (36.01 N, 5.61 W), 10 m wind (m/s, from °)\n")
    series = {d: at_tarifa(outs[d][0]) for d in ("d01", "d02", "d03") if outs[d]}
    if series:
        stamps = next(iter(series.values()))[0]
        head = " | ".join(f"{d} ({'land' if series[d][3] > 0.5 else 'sea'})" for d in series)
        print(f"| Time (day hour UTC) | {head} |\n|---|" + "---|" * len(series))
        for i, stamp in enumerate(stamps):
            cells = " | ".join(f"{series[d][1][i]:.1f} from {series[d][2][i]:.0f}°" if i < len(series[d][1]) else "–" for d in series)
            print(f"| {stamp} | {cells} |")


if __name__ == "__main__":
    main()
