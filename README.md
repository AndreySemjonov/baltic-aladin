# Baltic ALADIN

Open weather-model wind for Baltic kite spots, prepared for a phone app:

- **ALADIN 2.3 km** (ČHMÚ) read at each kite spot, as one small JSON file.
- **A wind map** of the Latvian coast, Gulf of Riga and western Estonia from
  **MET Nordic 1 km** (MET Norway): one small image per hour for the next ~58 hours.
- **More maps of the Latvian coast:** **HARM-DK 2 km** (DMI, 60 hours), **HARMONIE
  2.5 km** (FMI, ~66 hours) and **ALADIN 2.3 km** (ČHMÚ, 72 hours, up to about 57.0°N).
- **Wind maps of the whole Baltic** from **ICON-EU 7 km** (DWD, 5 days), **ECMWF 0.25°**
  (15 days) and **GFS 0.25°** (NOAA, 16 days).
- **A blend** of all of them for the Latvian coast, 10 days.
- **Water level and water temperature** at the spots from **NEMO-EST** (Estonian
  Environment Agency and TalTech, about 1 km, 5 days, twice a day).

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

**https://andreysemjonov.github.io/baltic-aladin/harm-dk/map.json**: DMI's HARMONIE
DINI 2 km, same box and pixels as MET Nordic, hourly for 60 hours. DMI publishes each
hour as one 620 MB file of all fields over its whole domain; the script reads the
headers with small range requests and downloads only the rows of 10 m wind speed,
direction and gust that cross the box (simple packing decodes row by row), about 4 MB
per hour. `python dmi_map.py site/harm-dk`.

Shared code for the maps is in [`maplib.py`](maplib.py).

## Long range: ECMWF and GFS

Same box as ICON-EU, about 7 km per pixel (the models' 0.25° grid is about 15 x 28 km
here), land mask from ICON-EU.

- **https://andreysemjonov.github.io/baltic-aladin/ecmwf/map.json**: ECMWF IFS open
  data, 00 and 12 UTC runs, every 3 hours to 144 hours and every 6 hours to 360; only the
  10 m wind and gust fields are downloaded from each step (about 3 MB) using its index.
  `python ecmwf_map.py site/ecmwf`.
- **https://andreysemjonov.github.io/baltic-aladin/gfs/map.json**: NOAA GFS, every 3 hours
  to 384 hours, cut to the box by NOAA's server (about 20 KB per step).
  `python gfs_map.py site/gfs`.

## Blend

**https://andreysemjonov.github.io/baltic-aladin/blend/map.json**: the maps above mixed
per pixel and hour on the MET Nordic pixels, with fixed weights (`WEIGHTS` in
[`blend_map.py`](blend_map.py)): wind and gust as weighted means, direction as a
speed-weighted vector mean. Where or when a model has no data (ALADIN north of 57°N,
the 2-3 day models after their last hour), the others share its weight. Hourly to 72
hours, then every 3 hours to 240. `python blend_map.py site` after building the others.

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

Map data: **Danish Meteorological Institute (DMI), HARMONIE DINI forecast, open data,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**, from
https://opendatadocs.dmi.govcloud.dk/. The frames are derived from it (cropped,
interpolated and reprojected).

Map data: **ECMWF, IFS open data, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**,
from https://www.ecmwf.int/en/forecasts/datasets/open-data. The frames are derived from it.

Map data: **NOAA/NCEP, GFS** (public domain), from https://nomads.ncep.noaa.gov/.

The blend is derived from all of the map data above and carries their credits.

Data: **ČHMÚ – Czech Hydrometeorological Institute, ALADIN model, open data,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)**, from
https://opendata.chmi.cz/meteorology/weather/nwp_aladin/. The published JSON and the
ALADIN map are derived from it (values read at points; cropped, interpolated and
reprojected) and carry the same licence and credit.

Code: MIT licence, see [LICENSE](LICENSE). Not affiliated with ČHMÚ, MET Norway, DWD, FMI, DMI, ECMWF or NOAA.

## NEMO-EST water level and temperature

The Estonian Environment Agency (Keskkonnaagentuur) publishes its sea model NEMO-EST
(made with TalTech) as open data, CC BY 4.0, through the
[KAIA open data service](https://avaandmed.keskkonnaportaal.ee/): one NetCDF file of
about 75 MB per forecast day. [`nemo.py`](nemo.py) finds the newest run through the
service's API, reads each spot's nearest sea cell (within 5 km) and publishes:

**https://andreysemjonov.github.io/baltic-aladin/nemo.json**

```json
{"run": "2026100200", "start": 1790902800, "step": 3600,
 "spots": {"haademeeste": {"lat": 58.083, "lon": 24.4667, "km": 0.6,
   "level": [4.8, …], "temperature": [16.21, …]}},
 "outside": ["pavilosta", …]}
```

- `level`: sea surface height in cm above the model's geoid (its own zero; a gauge
  reads a few tens of cm differently, so line it up with a measurement).
- `temperature`: sea surface temperature, °C.
- The model covers Estonia and the Gulf of Riga, from about 56.94°N and 21.55°E;
  Liepāja, Pāvilosta and Užava are outside.

Source: Keskkonnaagentuur, NEMO-EST model data, CC BY 4.0.
