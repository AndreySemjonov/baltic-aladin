"""HARM-NL's air and rain as the regional tiles read them (knmi.Model().weather), at a few places:
to find why its rain drew an even block over the Netherlands (12.10.2026). The key from
KNMI_API_KEY, never printed."""
import sys

import numpy as np

sys.path.insert(0, ".")
import knmi

model = knmi.Model()
start = int(model.run.timestamp())
print("run", model.run, "file", model.name)
data = model.weather([start + 5 * 3600, start + 6 * 3600, start + 20 * 3600])
places = {"Amsterdam": (4.9, 52.37), "Brussels": (4.35, 50.85), "Hamburg": (10.0, 53.55), "North Sea": (3.0, 54.5)}
lon = np.array([[p[0] for p in places.values()]])
lat = np.array([[p[1] for p in places.values()]])
for t, d in sorted(data.items()):
    c, r, w = model.place(lon, lat)
    print(f"+{(t - start) // 3600} h")
    for key, grid in d.items():
        print(f"  {key}: min {np.nanmin(grid):.3f} max {np.nanmax(grid):.3f} mean {np.nanmean(grid):.3f}",
              dict(zip(places, np.round(model.sample(grid, c, r)[0], 3))))
