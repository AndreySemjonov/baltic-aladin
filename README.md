# Baltic ALADIN

Open weather-model wind for Baltic kite spots, prepared for a phone app:

- **ALADIN 2.3 km** (ČHMÚ) read at each kite spot, as one small JSON file.
- **A wind map** of the Latvian coast, Gulf of Riga and western Estonia from
  **MET Nordic 1 km** (MET Norway): one small image per hour for the next ~58 hours.

The Czech Hydrometeorological Institute (ČHMÚ) publishes ALADIN as open data,
but only as whole-domain GRIB files of 70–80 MB per variable and run. A GitHub
Actions job checks every hour for a new run (four a day), downloads 10 m wind
speed, direction and gusts, reads each spot in [`spots.json`](spots.json) and
publishes the result:

**https://andreysemjonov.github.io/baltic-aladin/aladin.json**

```json
{"model": "ALADIN 2.3 km", "run": "2026-09-30T06:00Z", "updated": "…",
 "times": [1790661600, …],
 "spots": {"pavilosta": {"wind": [2.0, …], "gust": [3.2, …], "direction": [143, …]}},
 "outside": ["haademeeste", …]}
```

- `times`: Unix seconds (UTC), hourly for 72 hours (54 hours for the 18 UTC run).
- `wind`, `gust`: m/s. `direction`: degrees, where the wind comes from.
- Each value is the four grid points around the spot, weighted by inverse
  distance, with no coastal shift. Read this way, the values match the ALADIN
  row of a commercial forecast site for Pāvilosta within about 0.05 m/s (wind
  and gusts, 34 hours compared).
- `outside`: spots beyond the model's area (it ends at about 57.1°N on the
  Latvian coast).

Run it yourself: `pip install eccodes`, then `python extract.py` (newest run) or
`python extract.py 2026093006`.

## Wind map

**https://andreysemjonov.github.io/baltic-aladin/map/map.json** lists the frames of
the newest MET Nordic run (rebuilt every 3 hours): 55.6-59.1°N, 20.4-25.4°E,
about 1 km per pixel, 300 x 390 pixels, in Web Mercator (the projection of web and
phone maps), so an image lines up with a map by its bounds. Each PNG pixel stores
the wind: red = speed m/s x 5, green = direction (from) x 256/360, blue = gust m/s
x 5. About 70 KB per hour.

`pip install numpy pillow`, then `python metnordic_map.py site/map`.

## Credits and licence

Map data: **MET Norway, MET Nordic forecast, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**,
from https://thredds.met.no/. The map frames are derived from it and carry the same
licence and credit.

Data: **ČHMÚ – Czech Hydrometeorological Institute, ALADIN model, open data,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**, from
https://opendata.chmi.cz/meteorology/weather/nwp_aladin/. The published JSON is
derived from it (values read at points) and carries the same licence and credit.

Code: MIT licence, see [LICENSE](LICENSE). Not affiliated with ČHMÚ.
