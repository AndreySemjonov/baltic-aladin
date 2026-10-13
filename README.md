# Baltic ALADIN

Open weather, sea and map data for kite spots, prepared for a phone app (MeteoGust). It
started with ALADIN at the Baltic spots and now covers:

- **ALADIN 2.3 km** (ČHMÚ) read at each kite spot, as one small JSON file.
- **Wind maps** of Lithuania, Latvia and Estonia from **MET Nordic 1 km** (MET Norway),
  **HARM-DK 2 km** (DMI), **HARMONIE 2.5 km** (FMI) and **ALADIN 2.3 km** (ČHMÚ), plus
  **air temperature**, **rain** and **chance of rain** maps of the same area.
- **Wind maps of the whole Baltic** from **ICON-EU 7 km** (DWD, 5 days), **ECMWF 0.25°**
  (15 days) and **GFS 0.25°** (NOAA, 16 days), and **a blend** of all of them, 10 days.
- **Water level, water temperature, currents, sea ice and waves** from **NEMO-EST**, the
  **Copernicus Marine** Baltic models and DWD's **EWAM**, and the Estonian and Latvian
  **coastal gauges**.
- **World weather tiles** from **GFS** and **ECMWF** (wind everywhere; ECMWF also air,
  pressure, rain and cloud), every new run.
- **Regional weather tiles**: MET Nordic over its whole area with **ICON-D2**, **HARMONIE
  DINI**, **HARMONIE-AROME Netherlands**, **AROME**, **ICON-CH1** and **ICON-2I** blended in
  beyond it (wind, air and rain), and each of those models alone as its own wind set.
- **World coastline** and **rivers and lakes** tiles for the app's map.
- **Satellite clouds** (Meteosat) for the rain map, every 15 minutes of the last day.

## What is published where

| What | Where | Made by |
|---|---|---|
| Spot JSON, Baltic map frames (PNG + `map.json`) | GitHub Pages: `https://andreysemjonov.github.io/baltic-aladin/…` | `update.yml` |
| Satellite clouds | `https://files.meteogust.com/clouds/index.json` | `update.yml` (`clouds.py`) |
| World weather tiles | `https://files.meteogust.com/tiles/gfs/index.json`, `…/tiles/ecmwf/index.json` | `world.yml` |
| ICON-EU tiles | `…/tiles/icon-eu/` (wind), `…/tiles/icon-eu-wx/` (air and rain), `…/tiles/icon-eu-steer/` (the wind at 700 hPa that carries the showers, zoom 3, east and north m/s × 2 + 128) | `world.yml` |
| Regional weather tiles | `…/tiles/metnordic/` (wind), `…/tiles/metnordic-wx/` (air and rain), `…/tiles/{icon-d2,dini,harm-nl,arome,icon-ch1,icon-2i}/` (each model alone) | `regional.yml` |
| Coastline tiles | `…/tiles/coast/` | `coast.yml` (by hand) |
| Rivers and lakes tiles | `…/tiles/water/` | `water.yml` (by hand) |

Everything on `files.meteogust.com` is uploaded through the project's upload service (the
station-history Worker, `data.meteogust.com/…/upload/`, with the `ADMIN_INGEST_TOKEN`
secret); each set's `index.json` is written last, so a reader never sees a half-made run.

## ALADIN at the spots

The Czech Hydrometeorological Institute (ČHMÚ) publishes ALADIN as open data,
but only as whole-domain GRIB files of 70–80 MB per variable and run. A GitHub
Actions job checks for a new run (four a day), downloads 10 m wind
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
the newest MET Nordic run (rebuilt for each new run, hourly): 53.8-61.0°N, 16.5-28.5°E
(1.25 km pixels, 576 x 644), in Web Mercator (the projection of web and
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
- **https://andreysemjonov.github.io/baltic-aladin/world/map.json**: the quick world layer,
  GFS 0.25° between 78°S and 78°N, every 3 hours for 4 days, about 40 km per pixel, in the
  same format (GFS's own land mask). `python gfs_world.py site/world`. The finer world
  tiles below replace it over time.

## Blend

**https://andreysemjonov.github.io/baltic-aladin/blend/map.json**: the maps above mixed
per pixel and hour on the MET Nordic pixels, with fixed weights (`WEIGHTS` in
[`blend_map.py`](blend_map.py)): wind and gust as weighted means, direction as a
speed-weighted vector mean. Where or when a model has no data (the 2-3 day models after
their last hour), the others share its weight; a model covering only part of the box fades
out over its last 30 km. ALADIN (Kurzeme to Riga only) is not in the blend. Hourly to 72
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

## Air temperature map

[`temp_map.py`](temp_map.py) makes frames of the air temperature at 2 m and how it feels with
the wind (wind chill up to 10 °C, apparent temperature from 20 °C, blended between) from MET
Nordic 1 km, each forecast hour (about 58 hours), on the rain map's box at 2.5 km pixels:

**https://andreysemjonov.github.io/baltic-aladin/temp/map.json** (`"kind": "temperature"`)

- red: (air °C + 40) x 3 + 1, green: (feels like °C + 40) x 3 + 1, blue: (sea-level pressure
  hPa - 940) x 2 for the app's isobars; 0 = no data.

## World weather tiles

[`world_tiles.py`](world_tiles.py) (workflow `world.yml`, hourly; one job per model) turns each
new run of **GFS 0.25°** (4 runs a day, every 3 hours to 384 hours) and **ECMWF 0.25° open data**
(00 and 12 UTC, every 3 hours to 144 hours, then every 6 to 360) into tile packs on the web
map's grid (Web Mercator, z/x/y with y from the north, zooms 0-3; 256-pixel tiles, 180 at
zoom 3, the models' own detail). The tiles hold values, not colours; the app colours them.

- **Pack format "MGT1"**: the bytes `MGT1`, the header's length (4 bytes, little-endian), the
  header (JSON: `{"sizes", "zooms", "tiles": {"z/x/y": [offset from the header's end, length]}}`)
  and the PNG tiles, so the app reads the header and then only the tiles it shows.
- **Wind** (`<run>/<unix time>.pack`, both models): red = wind towards the east m/s x 4 + 128,
  green = towards the north m/s x 4 + 128, blue = gust m/s x 5.
- **Air** (`<time>-temp.pack`, ECMWF): as the temperature map (air, feels like, sea-level
  pressure). **Rain** (`<time>-rain.pack`, ECMWF): red = sqrt(mm an hour) x 50, green = cloud
  cover x 255.
- **Land** (`<run>/land.pack`, once per run): the model's land-sea mask, 0 sea - 255 land.
- **`tiles/<model>/index.json`**: model, credit, run, encoding, land pack and the frames.

```
python world_tiles.py gfs|ecmwf              build and upload the newest run, if new
python world_tiles.py gfs|ecmwf --force      even if it is the published one
python world_tiles.py gfs|ecmwf --local DIR  write to DIR, no upload (--steps 0,24 for a test)
```

## Regional weather tiles

[`regional_tiles.py`](regional_tiles.py) (workflow `regional.yml`, every 3 hours) makes the same
packs at zooms 4-6 for the newest **MET Nordic** run over its whole area (the Nordic countries
and the Baltic; read at every second cell, 2 km), hourly for the first day and every 3 hours
after. Beyond MET Nordic the other regional models are blended in (MET Nordic first; the others
averaged by their weights where they overlap), each fading out over the last 50 km to its
area's edge:

| Model | Provider | Area | Module |
|---|---|---|---|
| ICON-D2 2.2 km | DWD | Germany, Benelux, Denmark, the Alps, western Poland and Czechia | [`icond2.py`](icond2.py) |
| HARMONIE DINI 2 km | DMI | British Isles, North Sea, Denmark, the Channel | [`dini.py`](dini.py) |
| HARMONIE-AROME Netherlands (HARM-NL) 2 km | KNMI (free API key) | Netherlands, Belgium, southern North Sea | [`knmi.py`](knmi.py) |
| AROME 0.01° (about 1.3 km) | Météo-France (public OVH bucket) | France, the Channel, Benelux, western Germany, Switzerland, northern Italy | [`arome.py`](arome.py) |
| ICON-CH1 1 km | MeteoSwiss (STAC API) | Switzerland, the Alps, northern Italy's lakes | [`icon_ch1.py`](icon_ch1.py) |
| ICON-2I 2.2 km | ItaliaMeteo (MeteoHub) | Italy, its islands, the Adriatic, Corsica | [`icon2i.py`](icon2i.py) |

- **Wind** (`tiles/metnordic/`): RGBA, red/green/blue as the world tiles, alpha = how much the
  models count there (255 inside, 0 outside), so the app draws them over ECMWF's world.
- **Air and rain** (`tiles/metnordic-wx/`, `--set weather`, every third cell, 3 km):
  `<time>-temp.pack` and `<time>-rain.pack` as the world's, with the same alpha.
- **Each model alone** (`tiles/icon-d2/`, `dini/`, `harm-nl/`, `arome/`, `icon-ch1/`,
  `icon-2i/`): after the wind set, each regional model's own wind tiles and index, for the
  app's model menu.
- The index's `models` lists each model used in that run (its run and hours) or why it was
  left out; a model that can't be read is skipped and the rest go on.

```
python regional_tiles.py                  make and upload the newest run when it is new
python regional_tiles.py --set weather    the air and rain set
python regional_tiles.py --force          even when published
python regional_tiles.py --local DIR      write the packs to DIR (--hours N for fewer)
```

## World coastline and rivers

- [`coast_tiles.py`](coast_tiles.py) (`coast.yml`, by hand): how far each point is from the
  shore, from Natural Earth's 1:10 million land and lakes, so the app can split the weather's
  colours between land and sea outside the Baltic. Zooms 5-8, 256-pixel grey tiles (127.5 on
  the shore), one pack per zoom-5 tile with a coast; `tiles/coast/`.
- [`water_tiles.py`](water_tiles.py) (`water.yml`, by hand, with a dry-run option): the world's
  rivers (HydroRIVERS, from 50 km² of catchment) and lakes (HydroLAKES, from 10 ha) as vector
  tiles, zooms 0-10, made with GDAL and tippecanoe and packed as above; `tiles/water/`. The
  Baltic box is left out (the app's own OpenStreetMap lines are finer there).

## Credits and licence

Code: MIT licence, see [LICENSE](LICENSE). Data products are derived from the sources below
(cropped, interpolated, reprojected or read at points) and carry their licences and credits.
Not affiliated with any of the data providers.

| Data | Provider | Licence | Used for |
|---|---|---|---|
| ALADIN 2.3 km | ČHMÚ – Czech Hydrometeorological Institute ([open data](https://opendata.chmi.cz/meteorology/weather/nwp_aladin/)) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | spots, ALADIN map |
| MET Nordic 1 km, MEPS ensemble | MET Norway ([thredds.met.no](https://thredds.met.no/)) | CC BY 4.0 | wind, air, rain, chance-of-rain maps; regional tiles |
| ICON-EU, ICON-D2, EWAM | Deutscher Wetterdienst (DWD) ([opendata.dwd.de](https://opendata.dwd.de/)) | CC BY 4.0 | ICON-EU and rain maps, regional tiles, waves |
| HARMONIE 2.5 km | Finnish Meteorological Institute (FMI) ([open data](https://en.ilmatieteenlaitos.fi/open-data)) | CC BY 4.0 | HARMONIE map, rain map's clouds |
| HARMONIE DINI 2 km | Danish Meteorological Institute (DMI) ([open data](https://opendatadocs.dmi.govcloud.dk/)) | CC BY 4.0 | HARM-DK map, regional tiles |
| HARMONIE-AROME Netherlands | KNMI ([Data Platform](https://dataplatform.knmi.nl/)) | CC BY 4.0 | regional tiles |
| AROME | Météo-France ([meteo.data.gouv.fr](https://meteo.data.gouv.fr/)) | Etalab Open Licence 2.0 | regional tiles |
| ICON-CH1 | MeteoSwiss ([open data](https://opendatadocs.meteoswiss.ch/)) | open use, with the source named | regional tiles |
| ICON-2I | ItaliaMeteo ([MeteoHub](https://meteohub.agenziaitaliameteo.it/)) | CC BY 4.0 | regional tiles |
| IFS open data 0.25° | ECMWF ([open data](https://www.ecmwf.int/en/forecasts/datasets/open-data)) | CC BY 4.0 | ECMWF map, world tiles |
| GFS 0.25° | NOAA/NCEP ([NOMADS](https://nomads.ncep.noaa.gov/)) | public domain | GFS and world maps, world tiles |
| NEMO-EST | Keskkonnaagentuur (Estonian Environment Agency) and TalTech ([KAIA](https://avaandmed.keskkonnaportaal.ee/)) | CC BY 4.0 | water level and temperature |
| Estonian coastal gauges | Keskkonnaagentuur, ilmateenistus.ee | as published by the agency | gauges |
| Latvian coastal gauges | LVĢMC (data.gov.lv) | CC0 | gauges |
| Baltic Sea physics and waves | E.U. Copernicus Marine Service Information ([marine.copernicus.eu](https://marine.copernicus.eu/)) | Copernicus Marine licence (free account) | water, sea ice and wave maps |
| Meteosat infrared and Cloud Mask | EUMETSAT (EUMETView) | open data; credit "Meteosat, EUMETSAT" | satellite clouds |
| Land polygons, coastline, lakes, rivers, borders | © OpenStreetMap contributors | [ODbL](https://www.openstreetmap.org/copyright) | `coast.png`, `map-lines.bin` |
| 1:10m land and lakes | Natural Earth | public domain | coastline tiles |
| HydroRIVERS (Lehner & Grill 2013), HydroLAKES (Messager et al. 2016) | HydroSHEDS (WWF) | HydroSHEDS licence; HydroLAKES CC BY 4.0 | rivers and lakes tiles |

The app shows the EUMETNET OPERA radar composite (open data, CC BY 4.0) before now; it reads
it directly, not through this repo. The blend is derived from all of the map data above and
carries their credits.

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
- **https://andreysemjonov.github.io/baltic-aladin/nemo-coast.json** has the same hours at
  about 1,800 points along the whole coast (sea cells within about 4 km of land, every
  third cell, about 3 km apart), for places that aren't in `spots.json`: `points` as
  [lat, lon], `level` per point in cm, `temperature` per point in tenths of a degree.
- The model covers Estonia and the Gulf of Riga, from about 56.94°N and 21.55°E;
  Liepāja, Pāvilosta and Užava are outside.

## Estonian coastal gauges

The Estonian Environment Agency publishes only the current reading of its coastal gauges
as a file; each gauge's page on ilmateenistus.ee carries its last 10 days in the charts.
[`ee_gauges.py`](ee_gauges.py) reads those pages once an hour (one page every 10 seconds)
and publishes the last 48 hours:

**https://andreysemjonov.github.io/baltic-aladin/ee-gauges.json**

```json
{"made": "2026-10-03T07:00Z",
 "gauges": {"Häädemeeste": {"start": 1790838000, "step": 3600,
   "level": [26.0, …], "temperature": [13.5, …]}},
 "failed": []}
```

- Gauges are named as in the agency's observations file. `level`: cm in EH2000;
  `temperature`: water °C; hourly from `start` (Unix seconds, UTC).
- `failed`: gauges not read this time; they keep their last hours.

## The coast

The maps' `land.png` (the app's paler land) follows a fine coastline: [`coast.png`](coast.png),
a land mask of the regional box at about 250 m made once by [`build_coast.py`](build_coast.py)
from OpenStreetMap's land polygons (© OpenStreetMap contributors, ODbL). The wind values are
the models' own: a step that replaced the sea near the shore with values from further out
(`SEA_FILL` in maplib.py) is switched off, since it ended in a cliff at the shoreline where
the models fall off gradually over a few km.

## Water map

[`water_map.py`](water_map.py) makes map frames of sea level, water temperature and the
surface current for Lithuania, Latvia and Estonia (the regional maps' box, 1.25 km pixels,
hourly for the NEMO-EST run's 5 days):

**https://andreysemjonov.github.io/baltic-aladin/water/map.json** (`"kind": "water"`)

- **Models:** NEMO-EST (about 1 km) in Estonia and the Gulf of Riga, the Copernicus Marine
  Baltic model (about 1.7 km, [`copernicus.py`](copernicus.py)) elsewhere, crossfaded over
  20 km inside NEMO's edge. Copernicus needs a free Copernicus Marine account; the workflow
  reads it from the secrets `COPERNICUSMARINE_SERVICE_USERNAME` and
  `COPERNICUSMARINE_SERVICE_PASSWORD`. Without them the map is NEMO-EST only.
- **Heights:** each model is shifted to the gauges (`levelOffset`, `copernicusOffset`), and
  what is left at each gauge (`gaugeDifferences`, cm) nudges the map nearby, fading out over
  about 25 km. Levels are cm on EH2000 = LAS-2000,5.
- red: level + 128 (0 = outside the models; land holds its nearest sea value), green: water
  temperature × 8, blue: current m/s × 100, alpha: current direction (towards) × 256/360.
  Each frame and the manifest carry `levelRange` and `temperatureRange`.
- **Sea ice** from the same Copernicus model goes beside it, **`ice/map.json`** (`"kind": "ice"`):
  red = 1 + ice cover x 200, green = ice thickness cm; `iceMax` is the run's largest cover.

**https://andreysemjonov.github.io/baltic-aladin/gauges.json**: the Estonian and Latvian coastal
gauges' last days on one height system ([`gauges.py`](gauges.py); the Latvian gauges,
LVĢMC open data CC0, corrected from each gauge's own zero to LAS-2000,5).

## Rain map

[`rain_map.py`](rain_map.py) makes map frames of the forecast rain from MET Nordic 1 km (each
hour's precipitation, about 57 hours, rebuilt with the MET Nordic wind map) on the water map's
box at 2.5 km pixels:

**https://andreysemjonov.github.io/baltic-aladin/rain/map.json** (`"kind": "rain"`)

- red: sqrt(precipitation mm in the hour) × 50 (0 = none). The app shows the radar before now
  and its own 2-hour nowcast from the radar's motion.
- green: how cloudy it looks × 255, from HARM-FI's low, middle and high cloud (FMI, CC BY 4.0;
  low counts in full, middle 0.6, high 0.3), else MET Nordic's total cover × 0.6.
- Days 3-5: [`rain_icon.py`](rain_icon.py) makes the same frames from ICON-EU 7 km (DWD, CC BY
  4.0; rain and its low, middle and high cloud) per ICON-EU main run, published at
  `rain-icon/map.json`; [`rain_join.py`](rain_join.py) adds those after MET Nordic's last hour
  to `rain/map.json` (frames marked `"models": ["ICON-EU"]`).
- Chance of rain: [`rain_chance.py`](rain_chance.py) reads MET Norway's MEPS ensemble (open
  data, CC BY 4.0; the newest runs lagged over 6 hours, 30 members at 2.5 km, about 61 hours,
  read at 5 km) and makes, for each hour, the share of members with 0.5 mm or more in that hour,
  on the same box and pixels: **`rain-chance/map.json`** (`"kind": "rain-chance"`), red =
  chance × 255. A new set comes every hour (`docs/runs.json`: `rain-chance`), about 210 MB read
  and under a minute.
- Satellite clouds: [`clouds.py`](clouds.py) makes one grey picture with transparency per
  15 minutes of the last day from Meteosat's infrared (MTG FCI 10.5 µm) and Cloud Mask (MSG),
  read from EUMETSAT's EUMETView WMS for the rain map's box, and uploads it to
  `files.meteogust.com/clouds/`.

## Map lines

[`build_lines.py`](build_lines.py) (run by hand, like `build_coast.py`) makes `map-lines.bin`, the
lines the app draws over its map colors: the coastline (OpenStreetMap's coastline lines, simplified
for zoomed out and in full detail for closer in; `osm-cache/`, not in git), named lakes, rivers and
country borders on land, simplified, each with a detail level for the zoom (about 6.5 MB). Lakes,
rivers and borders come from OpenStreetMap through the Overpass API, in 1° tiles cached in
`lines-cache/` (not in git; about 300 MB). `map-lines.bin` is © OpenStreetMap contributors and
available under the Open Database License (ODbL): https://www.openstreetmap.org/copyright

## Wave map

[`wave_map.py`](wave_map.py) makes map frames of the waves on the water map's box at 2.5 km
pixels from the **Copernicus Baltic wave forecast** (WAM at 1 nautical mile, up to 10 days:
hourly for 3 days, then every 3 hours); when it can't be read (no login, late), from DWD's
European wave model **EWAM** (00 and 12 UTC runs, 78 hours, hourly, about 5.5 km):

**https://andreysemjonov.github.io/baltic-aladin/waves/map.json** (`"kind": "waves"`)

- red: significant wave height m × 40 + 1 (0 = no data; land holds its nearest sea value),
  green: mean wave period s × 10, blue: mean wave direction (from) × 256/360.
- Each frame and the manifest carry `heightMax` (m). A new run is built when one is online,
  like ICON-EU (`docs/runs.json`: `waves`).

## Workflows

| Workflow | Schedule | What it does |
|---|---|---|
| `update.yml` | every 15 min (`7,22,37,52 * * * *`) | ALADIN spots, NEMO-EST, gauges, satellite clouds; rebuilds each Baltic map whose model has a new run (others are copied from the live site by [`carry.py`](carry.py)), the blend, and deploys GitHub Pages; sends a short run report to the admin panel |
| `world.yml` | hourly (`41 * * * *`) | world tiles, one job each for GFS and ECMWF, only when the run is new |
| `regional.yml` | every 3 hours (`25 */3 * * *`) | regional wind tiles and each model alone; the air and rain set every 6 hours on the schedule and on every dispatched run |
| `coast.yml` | by hand | world coastline tiles |
| `water.yml` | by hand (`dry` input: build without upload) | world rivers and lakes tiles |
| `knmi-check.yml` | by hand | a look at KNMI's HARMONIE-AROME files with the API key (`tools/knmi_check.py`) |

GitHub often skips or delays scheduled runs, so a small external trigger (a Cloudflare Worker)
also starts the workflows through the GitHub API (`workflow_dispatch`): `update.yml` every 5
minutes when no run is going, `world.yml` hourly and `regional.yml` every 3 hours. Their own
cron schedules stay as a backup. A run with nothing new stops after its check.

## Running locally

Python 3 (the workflows use the runner's system `python3` in a venv):

```
pip install eccodes numpy pillow scipy
pip install h5py copernicusmarine   # only for the sea models (NEMO-EST, Copernicus)
```

Each script prints its usage; most take an output folder (`python metnordic_map.py site/map`)
or `--check` to print the newest run. The tile scripts upload by default and take
`--local DIR` to write the packs to a folder instead. `water_tiles.py` also needs GDAL
(`ogr2ogr`) and [tippecanoe](https://github.com/felt/tippecanoe) (the workflow builds 2.79.0).

## Secrets

Set as GitHub Actions secrets (names only; the scripts read them from the environment):

- `ADMIN_INGEST_TOKEN` (as `INGEST_TOKEN`): uploads to the file store (tiles, clouds) and the
  run report. Without it nothing is uploaded or reported.
- `KNMI_API_KEY`: KNMI Data Platform key for HARM-NL (free). Without it HARM-NL is left out.
- `COPERNICUSMARINE_SERVICE_USERNAME`, `COPERNICUSMARINE_SERVICE_PASSWORD`: a free Copernicus
  Marine account for the water, sea ice and wave maps. Without them the water map is NEMO-EST
  only and the waves come from EWAM.
