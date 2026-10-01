# Baltic ALADIN

Open weather-model wind for Baltic kite spots, prepared for a phone app:

- **ALADIN 2.3 km** (ČHMÚ) read at each kite spot, as one small JSON file.
- **A wind map** of the Latvian coast, Gulf of Riga and western Estonia from
  **MET Nordic 1 km** (MET Norway): one small image per hour for the next ~58 hours.
- **Two more maps of the Latvian coast:** **HARMONIE 2.5 km** (FMI, ~66 hours) and
  **ALADIN 2.3 km** (ČHMÚ, 72 hours, up to about 57.0°N).
- **A wind map of the whole Baltic** from **ICON-EU 7 km** (DWD) for 5 days.

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
x 5. About 70 KB per hour. `land.png` (same pixels, grayscale) is the land fraction x
255, 0 = sea, for drawing the wind strongly over the sea and faintly over land.

`pip install numpy pillow`, then `python metnordic_map.py site/map`.

## HARMONIE and ALADIN maps

**https://andreysemjonov.github.io/baltic-aladin/harmonie/map.json**: FMI's HARMONIE
2.5 km, the same box and pixels as the MET Nordic map (and its land mask), hourly for
about 66 hours, rebuilt with each deploy (FMI runs every 3 hours). FMI's open data
service cuts the grid to the box on request, about 20 MB per run.
`python harmonie_map.py site/harmonie`.

**https://andreysemjonov.github.io/baltic-aladin/aladin/map.json**: ALADIN 2.3 km,
55.6-57.25°N, 20.4-25.4°E (Kurzeme to Riga and Jūrmala), hourly for 72 hours, rebuilt
for each new ALADIN run from the files the spot extraction downloads
(`ALADIN_KEEP=aladin-files python extract.py`, then
`python aladin_map.py site/aladin aladin-files`). The model ends at about 57.0-57.2°N,
so these frames have an alpha channel: 0 where there is no model data.

Shared code for the maps is in [`maplib.py`](maplib.py).

## Wind map of the Baltic, 5 days

**https://andreysemjonov.github.io/baltic-aladin/icon-eu/map.json**: the same format
for ICON-EU 7 km, 53.5-66°N, 9-31°E (Denmark to the Gulf of Bothnia), 352 x 403
pixels (one per model grid step), hourly to 78 hours and then every 3 hours to 120
hours, about 130 KB per frame. Rebuilt for each 00/06/12/18 UTC run, about 4 hours
after it starts. Wind is interpolated bilinearly from the model's grid; the gust is the
maximum of the last hour. `land.png` is DWD's land plus lake fraction (lakes count as land).

DWD publishes each variable and hour as a whole-Europe file, so a run means about
280 downloads of about 1.1 MB. `pip install eccodes numpy pillow`, then
`python iconeu_map.py site/icon-eu` (newest complete run) or
`python iconeu_map.py site/icon-eu 2026093012`.

## Credits and licence

Map data: **MET Norway, MET Nordic forecast, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**,
from https://thredds.met.no/. The map frames are derived from it and carry the same
licence and credit.

Map data: **Deutscher Wetterdienst (DWD), ICON-EU, open data,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**, from
https://opendata.dwd.de/weather/nwp/icon-eu/. The frames are derived from it
(cropped, interpolated and reprojected) and carry the same licence and credit.

Map data: **Finnish Meteorological Institute (FMI), HARMONIE forecast, open data,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**, from
https://en.ilmatieteenlaitos.fi/open-data. The frames are derived from it (interpolated
and reprojected) and carry the same licence and credit.

Data: **ČHMÚ – Czech Hydrometeorological Institute, ALADIN model, open data,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**, from
https://opendata.chmi.cz/meteorology/weather/nwp_aladin/. The published JSON and the
ALADIN map are derived from it (values read at points; cropped, interpolated and
reprojected) and carry the same licence and credit.

Code: MIT licence, see [LICENSE](LICENSE). Not affiliated with ČHMÚ, MET Norway, DWD or FMI.
