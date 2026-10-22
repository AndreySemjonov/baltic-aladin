"""The helper's jobs as the runner starts them (runner/scheduler.py; the owner, 21.10.2026: off GitHub,
the Tower first, then the Mac mini). Each job: its script and arguments, and when it is due:

- `watch`: [(Open-Meteo's open-data model, the hours its runs come at)]: as soon as a newer complete run
  is there (latest.json; the makers' release or a little after), `delay` hours after it at the earliest
  (a source we don't read from that bucket, e.g. ECMWF's own wave files 2 hours later);
- `every`: minutes between runs, for jobs built from our own published sets (the mixes, the places'
  files) or sources without a run record here.

`heavy`: a big job (several GB of memory, long): at most a few at once. `needs`: environment variables it
can't run without (skipped while missing). The same cadences as the GitHub workflows (21.10.2026).

`priority` (the owner, 21.10.2026: in the 00Z/12Z peaks the models riders look at first go first): 1 what a
rider opens first (the spots' forecasts and their inputs, the Mix, the Baltic's fine models, the rain now),
2 the other maps, 3 what can wait an hour (the far world, the seas, the ensembles, the air). Due jobs start
in that order; a 3 starts only while a slot stays free for a 1 or 2.
"""
from dataclasses import dataclass, field


@dataclass
class Job:
    name: str
    args: list
    watch: list = field(default_factory=list)
    every: int = 0
    delay: float = 0
    heavy: bool = False
    needs: list = field(default_factory=list)
    timeout: int = 150
    priority: int = 2


COPERNICUS = ["COPERNICUSMARINE_SERVICE_USERNAME", "COPERNICUSMARINE_SERVICE_PASSWORD"]
# The models regional_tiles.py makes besides MET Nordic: its --only name and Open-Meteo's open-data model (when each
# run is complete there).
REGIONAL_MODELS = [("icon-d2", "dwd_icon_d2"), ("dini", "dmi_harmonie_arome_europe"), ("harm-nl", "knmi_harmonie_arome_netherlands"),
                   ("arome", "meteofrance_arome_france_hd"), ("icon-ch1", "meteoswiss_icon_ch1"), ("icon-2i", "italia_meteo_arpae_icon_2i")]

JOBS = [
    # The world's models (world.yml).
    Job("world-gfs", ["world_tiles.py", "gfs"], watch=[("ncep_gfs025", 6)]),
    Job("world-ecmwf", ["world_tiles.py", "ecmwf"], watch=[("ecmwf_ifs", 6)], heavy=True),
    Job("world-icon", ["world_tiles.py", "icon"], watch=[("dwd_icon", 6)], priority=3),
    Job("world-gdps", ["world_tiles.py", "gdps"], watch=[("cmc_gem_gdps_15km", 12)], priority=3),
    Job("icon-eu", ["iconeu_tiles.py"], watch=[("dwd_icon_eu", 3)], heavy=True, priority=1),
    Job("ecmwf-waves", ["waves_tiles.py", "ecmwf"], watch=[("ecmwf_wam", 12)], delay=2.5, priority=3),
    Job("ewam", ["waves_tiles.py", "ewam"], watch=[("dwd_ewam", 12)], priority=3),
    # Europe's models from Open-Meteo's open data (bucket.yml, ukmo.yml).
    Job("bucket-icon-ch2", ["bucket_tiles.py", "icon-ch2"], watch=[("meteoswiss_icon_ch2", 6)]),
    Job("bucket-knmi-eu", ["bucket_tiles.py", "knmi-eu"], watch=[("knmi_harmonie_arome_europe", 1)]),
    Job("bucket-arome25", ["bucket_tiles.py", "arome25"], watch=[("meteofrance_arome_france0025", 3)]),
    Job("ukmo", ["ukmo_tiles.py"], watch=[("ukmo_uk_deterministic_2km", 3)]),
    # The regional models (regional.yml, nordic.yml): the MET Nordic blend (all seven models read at once: heavy) on
    # each new MET Nordic run, and each model's own set as a small job of its own on that model's new run, whatever
    # hour it comes (the owner, 21.10.2026: one job watching MET Nordic only left ICON-D2's 12Z waiting 58 min; "B":
    # ICON-D2's own set alone is ~1-3 GB, not the 9.5-11 GB whole). Each makes only what's new, in shadow mode too
    # (regional_tiles.py keeps the shadow's own record in /stats/shadow).
    Job("regional-wind", ["regional_tiles.py", "--set", "wind", "--blend"], watch=[("metno_nordic_pp", 1)], heavy=True, priority=1),
    Job("regional-weather", ["regional_tiles.py", "--set", "weather", "--blend"], watch=[("metno_nordic_pp", 1)], heavy=True),
    *[Job(f"regional-{name}", ["regional_tiles.py", "--set", "wind", "--only", name], watch=[(model, 1)], priority=1)
      for name, model in REGIONAL_MODELS],
    *[Job(f"regional-{name}-wx", ["regional_tiles.py", "--set", "weather", "--only", name], watch=[(model, 1)])
      for name, model in REGIONAL_MODELS],
    Job("harm-fi", ["harmfi_tiles.py"], every=180),
    Job("aladin", ["aladin_tiles.py"], every=360),
    Job("nordic-1km", ["metnordic_tiles.py"], watch=[("metno_nordic_pp", 1)], priority=1),
    Job("nordic-1km-wx", ["metnordic_tiles.py", "--set", "weather"], watch=[("metno_nordic_pp", 1)]),
    # Built from our published sets.
    Job("mix", ["mix_tiles.py"], every=60, heavy=True, priority=1),
    Job("mix-world", ["mix_world.py"], every=60),
    # Hourly (the owner, 22.10.2026, "option B"): each run remakes about a day of hours, the rest kept while under 3 h.
    Job("weather-mix-europe", ["weather_mix.py", "europe"], every=60, heavy=True, timeout=240),
    Job("weather-mix-world", ["weather_mix.py", "world"], every=60, heavy=True, timeout=240, priority=3),
    Job("series", ["series_tiles.py"], every=30, priority=1),
    Job("spot-points", ["spot_points.py"], every=30, priority=1),
    # The spots' short runs and ensembles.
    Job("gfs013", ["gfs013_points.py"], watch=[("ncep_gfs013", 6)], priority=1),
    Job("ecmwf-short", ["ecmwf_short_points.py"], watch=[("ecmwf_ifs", 6)], priority=1),
    Job("icon-eu-short", ["iconeu_short_points.py"], watch=[("dwd_icon_eu", 3)], priority=1),
    Job("ens", ["ens_points.py"], watch=[("ecmwf_ifs", 12)], delay=1.5, heavy=True, priority=3),
    # Rain, air, seas.
    Job("chance", ["chance_tiles.py"], every=180),
    Job("rain-now", ["rain_now.py"], every=60, priority=1),
    Job("air", ["air_points.py"], every=180, priority=3),
    Job("sea", ["water_composite.py"], every=720, heavy=True, needs=COPERNICUS, priority=3),
    Job("waves", ["waves_composite.py"], every=720, needs=COPERNICUS, priority=3),
    Job("sea-world", ["sea_world.py"], every=1440, heavy=True, needs=COPERNICUS, timeout=240, priority=3),
]
