"""The ops panel's three summaries beside the live view (runner/ops/server.py; the owner, 21.10.2026: "all in one
place... most important to see how far behind we are of models release... what each process takes and where we
lack capacity"; the mockup docs/mockups/ops-panel-v2-2026-10-21.html in the app's repo):

- freshness(): each model, the maker's newest run (Open-Meteo's open-data record, latest.json: run and when it was
  complete) against ours (station-history /runs for the spot forecasts, each set's map index), who is making the next
  one (GitHub's runs in progress, the home runner's live jobs), our usual delay; our published runs and the makers'
  arrivals kept in STATS for the 24 h strips.
- capacity(): the last 24 hours from the runner's records: each job's runs, CPU, peak memory and waits (from due to
  started), the machine by hour (CPU by WRF and jobs, memory, jobs waiting).
- admin(): the feeds' ages (station-history's WATCHED, from the file store), and admin.meteogust.com's overview when
  the admin service token is in the env file (read-only; changes stay on admin.meteogust.com).
"""
import json
import os
import statistics
import threading
import time
import urllib.request
from datetime import datetime, timezone

HOME = os.environ.get("MG_HOME", "/var/lib/meteogust")
STATS, LIVE = f"{HOME}/stats", f"{HOME}/live.json"
ENV_FILE = os.environ.get("MG_ENV", "/root/.meteogust.env")
AGENT = {"User-Agent": "MeteoGust ops panel (data.meteogust.com)"}
BUCKET = "https://openmeteo.s3.amazonaws.com/data_spatial/"
FILES = "https://files.meteogust.com/"
GITHUB_RUNS = "https://api.github.com/repos/AndreySemjonov/baltic-aladin/actions/runs"
GITHUB_WORKFLOWS = "https://api.github.com/repos/AndreySemjonov/baltic-aladin/actions/workflows/"

# [app key, label, group, Open-Meteo's model (None: not in its record), our set, every hours, GitHub workflow and
#  title word, the runner's jobs]
MODELS = [
    ("metno_nordic", "MET Nordic 1 km", "Fine models, Europe", "metno_nordic_pp", "metnordic-1km", 1, ("nordic.yml", None), ["nordic-1km"]),
    ("knmi_harmonie_arome_europe", "HARM-EU (KNMI)", "Fine models, Europe", "knmi_harmonie_arome_europe", "knmi-eu", 1, ("bucket.yml", None), ["bucket-knmi-eu"]),
    ("knmi_harmonie_arome_netherlands", "HARM-NL", "Fine models, Europe", "knmi_harmonie_arome_netherlands", "harm-nl", 1, ("regional.yml", None), ["regional-harm-nl"]),
    ("icon_d2", "ICON-D2", "Fine models, Europe", "dwd_icon_d2", "icon-d2", 3, ("regional.yml", None), ["regional-icon-d2"]),
    ("meteofrance_arome_france_hd", "AROME 1.3 km", "Fine models, Europe", "meteofrance_arome_france_hd", "arome", 3, ("regional.yml", None), ["regional-arome"]),
    ("dmi_harmonie_arome_europe", "HARM-DK (DINI)", "Fine models, Europe", "dmi_harmonie_arome_europe", "dini", 3, ("regional.yml", None), ["regional-dini"]),
    ("meteoswiss_icon_ch1", "ICON-CH1", "Fine models, Europe", "meteoswiss_icon_ch1", "icon-ch1", 3, ("regional.yml", None), ["regional-icon-ch1"]),
    ("italia_meteo_arpae_icon_2i", "ICON-2I", "Fine models, Europe", "italia_meteo_arpae_icon_2i", "icon-2i", 12, ("regional.yml", None), ["regional-icon-2i"]),
    ("ukmo_uk_deterministic_2km", "UKV", "Fine models, Europe", "ukmo_uk_deterministic_2km", "ukmo", 3, ("ukmo.yml", None), ["ukmo"]),
    ("harm_fi", "HARM-FI", "Fine models, Europe", None, "harm-fi", 3, ("regional.yml", None), ["harm-fi"]),
    ("aladin", "ALADIN", "Fine models, Europe", None, "aladin", 6, ("update.yml", None), ["aladin"]),
    ("icon_eu", "ICON-EU", "Regional and world models", "dwd_icon_eu", "icon-eu", 3, ("world.yml", "icon-eu"), ["icon-eu"]),
    ("meteoswiss_icon_ch2", "ICON-CH2", "Regional and world models", "meteoswiss_icon_ch2", "icon-ch2", 6, ("bucket.yml", None), ["bucket-icon-ch2"]),
    ("meteofrance_arome_france", "AROME-FR 2.5 km", "Regional and world models", "meteofrance_arome_france0025", "arome25", 3, ("bucket.yml", None), ["bucket-arome25"]),
    ("ecmwf_ifs", "ECMWF 9 km", "Regional and world models", "ecmwf_ifs", "ecmwf", 6, ("world.yml", "ecmwf"), ["world-ecmwf"]),
    ("gfs_global", "GFS", "Regional and world models", "ncep_gfs025", "gfs", 6, ("world.yml", "gfs"), ["world-gfs"]),
    ("icon_global", "ICON world", "Regional and world models", "dwd_icon", "icon", 6, ("world.yml", "icon"), ["world-icon"]),
    ("gem_global", "GDPS", "Regional and world models", "cmc_gem_gdps_15km", "gdps", 12, ("world.yml", "gdps"), ["world-gdps"]),
    ("ecmwf_wam", "ECMWF waves", "Waves", "ecmwf_wam", "ecmwf-waves", 12, ("world.yml", "ecmwf-waves"), ["ecmwf-waves"]),
    ("dwd_ewam", "EWAM (DWD)", "Waves", "dwd_ewam", "ewam", 12, ("world.yml", "ewam"), ["ewam"]),
    ("wrf_tarifa", "WRF Tarifa 1 km", "Our own", None, "wrf-tarifa", 12, (None, None), []),
]
# Products riders see that are made from our own sets: [label, set, the sets it's made from, the runner's job, workflow]
PRODUCTS = [
    ("The Mix (Europe)", "mix", ["metnordic-1km", "dini", "harm-fi", "arome", "icon-d2", "icon-ch1", "harm-nl", "icon-2i", "aladin",
                                 "ukmo", "icon-ch2", "arome25", "knmi-eu", "icon-eu", "ecmwf", "gfs", "wrf-tarifa"], "mix", "mix.yml"),
    ("Weather mix (Europe)", "mix-wx", [], "weather-mix-europe", "weather-mix.yml"),
    ("Rain now", "rain-now", [], "rain-now", "rain-now.yml"),
]
# station-history's WATCHED (the admin alerts): key, name, file, usual, late after (s).
WATCHED = [
    ("radar", "Radar", "tiles/radar/index.json", "5 min", 1800), ("sat", "Satellite clouds", "tiles/sat/index.json", "15 min", 2700),
    ("rain-now", "Rain nowcast", "tiles/rain-now/index.json", "hourly", 3 * 3600),
    ("mix", "Wind mix (Europe, fine)", "tiles/mix/index.json", "hourly", 3 * 3600),
    ("mix-world", "Wind mix (world)", "tiles/mix-world/index.json", "6-hourly", 12 * 3600),
    ("mix-wx", "Weather mix (Europe, fine)", "tiles/mix-wx/index.json", "3-hourly", 9 * 3600),
    ("mix-world-wx", "Weather mix (world)", "tiles/mix-world-wx/index.json", "3-hourly", 9 * 3600),
    ("ens", "Ensemble at the spots", "ens/index.json", "12-hourly", 20 * 3600),
    ("metnordic-1km", "MET Nordic", "tiles/metnordic-1km/index.json", "hourly", 3 * 3600),
    ("metnordic-1km-wx", "MET Nordic rain and air", "tiles/metnordic-1km-wx/index.json", "hourly", 3 * 3600),
    ("chance", "Chance of rain", "tiles/chance/index.json", "3-hourly", 8 * 3600), ("arome", "AROME", "tiles/arome/index.json", "3-hourly", 8 * 3600),
    ("icon-d2", "ICON-D2", "tiles/icon-d2/index.json", "3-hourly", 8 * 3600), ("dini", "HARM-DK", "tiles/dini/index.json", "3-hourly", 8 * 3600),
    ("harm-fi", "HARM-FI", "tiles/harm-fi/index.json", "3-hourly", 8 * 3600), ("icon-eu", "ICON-EU", "tiles/icon-eu/index.json", "6-hourly", 12 * 3600),
    ("gfs", "GFS", "tiles/gfs/index.json", "6-hourly", 12 * 3600), ("aladin", "ALADIN", "tiles/aladin/index.json", "6-hourly", 14 * 3600),
    ("ecmwf", "ECMWF", "tiles/ecmwf/index.json", "6-hourly", 16 * 3600),
    ("ecmwf-waves", "ECMWF waves", "tiles/ecmwf-waves/index.json", "6-hourly", 16 * 3600),
    ("wrf-tarifa", "WRF 1 km Tarifa (ours)", "tiles/wrf-tarifa/index.json", "twice a day", 16 * 3600),
    ("sea", "Europe's seas", "tiles/sea/index.json", "twice a day", 16 * 3600), ("waves", "Waves", "tiles/waves/index.json", "twice a day", 16 * 3600),
    ("sea-world", "World seas", "tiles/sea-world/index.json", "daily", 30 * 3600),
    ("helper-aladin", "ALADIN spots file", "helper/aladin.json", "6-hourly", 14 * 3600),
    ("helper-gauges", "Gauges file", "helper/gauges.json", "15 min", 5400),
]


def fetch(url, headers=None, timeout=20, method="GET"):
    request = urllib.request.Request(url, headers={**AGENT, **(headers or {})}, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply if method == "HEAD" else json.load(reply)


def run_unix(text):
    try:
        return int(datetime.strptime(str(text)[:17], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        try:
            return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            return None


def jsonl(path, last=5000):
    try:
        with open(path) as f:
            lines = f.readlines()[-last:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def append(path, record):
    try:
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def env_value(name):
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if "=" in line and line.split("=", 1)[0].strip() == name:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def live_state():
    try:
        with open(LIVE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def median(values):
    return statistics.median(values) if values else None


# MARK: GitHub's runs

# The workflows that make the maps, for the Pipeline's GitHub bars (when each job started and ended).
PIPE_WORKFLOWS = ["regional.yml", "world.yml", "bucket.yml", "nordic.yml", "ukmo.yml", "update.yml", "mix.yml", "mix-world.yml",
                  "weather-mix.yml", "chance.yml", "sea.yml", "waves.yml", "water.yml"]


# A run's states before completed: queued, in progress, and "pending"/"waiting"/"requested" (in a concurrency group
# behind another run, or waiting for approval).
WAITING_OR_GOING = ("queued", "in_progress", "pending", "waiting", "requested")


class GithubRuns:
    """GitHub's runs of the last 40 hours from the public API (60 calls an hour without a token): each map workflow's
    last 100 once, then every 5 minutes the newest 100 of all (about 2 hours of runs: 1685 runs in 36 h on 21.10.2026).
    Also what's running or queued now, for the freshness (it asked twice every 2 minutes before)."""

    SAVED = f"{STATS}/_github_runs.json"  # kept across the server's reloads (each reload cost 13 calls otherwise)

    def __init__(self):
        self.lock = threading.Lock()
        self.runs = {}        # id -> [workflow, title, start, end or None, status, conclusion]
        self.page = set()     # the ids in the newest fetch (an old "in progress" off it is stale)
        self.asked = {}       # id -> when a stale one was last asked for by itself
        self.at = 0
        self.backfilled = False
        self.retry_at = 0
        try:
            with open(self.SAVED) as f:
                saved = json.load(f)
            self.runs = {int(k): v for k, v in saved["runs"].items()}
            self.backfilled = bool(saved.get("backfilled"))
            self.at = saved.get("at", 0)
        except (OSError, ValueError, KeyError):
            pass

    def take(self, items):
        for r in items:
            done = r.get("status") == "completed"
            self.runs[r["id"]] = [os.path.basename(r.get("path", "")), r.get("display_title") or "", run_unix(r.get("run_started_at")),
                                  run_unix(r.get("updated_at")) if done else None, r.get("status"), r.get("conclusion")]

    def refresh(self, every=300):
        if time.time() - self.at < every:
            return
        self.at = time.time()
        with self.lock:
            if not self.backfilled and time.time() >= self.retry_at:
                failed = 0
                for wf in PIPE_WORKFLOWS:
                    try:
                        self.take(fetch(f"{GITHUB_WORKFLOWS}{wf}/runs?per_page=100").get("workflow_runs", []))
                    except Exception:
                        failed += 1
                        break  # (rate-limited: the rest would fail too)
                self.backfilled = not failed
                self.retry_at = time.time() + 1800
            try:
                newest = fetch(f"{GITHUB_RUNS}?per_page=100").get("workflow_runs", [])
                self.take(newest)
                self.page = {r["id"] for r in newest}
            except Exception:
                pass
            # A run that fell off the newest page while in progress never got its end (the weather mix's 21:48 run
            # read "going" for 7 h in the Pipeline, 22.10.2026): asked for by itself, each at most every 30 minutes and
            # 3 a refresh (the public API's 60 calls an hour, shared with the newest page and the workflows' records).
            # Any state but completed (25.10.2026: the weather mix's 16:46 UTC run, last seen "pending" behind the run
            # before it in its concurrency group, stayed open and claimed each of the Tower's publishes for 12 h).
            if self.page:
                now = time.time()
                stale = [k for k, v in self.runs.items() if v[4] != "completed" and k not in self.page
                         and now - self.asked.get(k, 0) > 1800]
                for k in stale[:3]:
                    self.asked[k] = now
                    try:
                        self.take([fetch(f"{GITHUB_RUNS}/{k}")])
                    except Exception:
                        break
            cut = time.time() - 40 * 3600
            self.runs = {k: v for k, v in self.runs.items() if (v[2] or 0) >= cut}
            try:
                with open(self.SAVED + ".tmp", "w") as f:
                    json.dump({"at": self.at, "backfilled": self.backfilled, "runs": self.runs}, f)
                os.replace(self.SAVED + ".tmp", self.SAVED)
            except OSError:
                pass

    def running(self):
        with self.lock:
            return [{"wf": v[0], "title": v[1], "started": v[2], "status": v[4]} for k, v in self.runs.items()
                    if v[4] in WAITING_OR_GOING and k in self.page]

    def pipe(self):
        """[workflow, the title's last word (world.yml runs one model or "(all)"), start, end or None] of the map
        workflows' runs that didn't fail. One without an end over 6 hours old (GitHub's own job limit) is left out: its
        end was missed, and as "going" it would claim every publish since."""
        cut = time.time() - 6 * 3600
        with self.lock:
            return sorted([[v[0], v[1].split()[-1] if v[1] else "", v[2], v[3]] for v in self.runs.values()
                           if v[0] in PIPE_WORKFLOWS and v[2] and v[5] in (None, "success") and (v[3] or v[2] >= cut)],
                          key=lambda r: r[2])


GH = GithubRuns()


# MARK: Freshness

class Freshness:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}
        self.github = {"at": 0, "runs": []}
        self.seen = {}  # (key, kind) -> run, to record our published runs once
        for p in jsonl(f"{STATS}/_published.jsonl", 3000):
            self.seen[(p["key"], p["kind"])] = p["run"]
        # The makers' runs already in _arrivals.jsonl (the scheduler writes those it watches; this, all the others:
        # the Pipeline's "available" moment, 21.10.2026).
        self.arrived = {(a.get("model"), a.get("run")) for a in jsonl(f"{STATS}/_arrivals.jsonl", 20000)}

    def github_running(self):
        """GitHub's runs in progress or queued (GH's cache, every 5 minutes)."""
        GH.refresh()
        self.github = {"at": GH.at, "runs": GH.running()}
        return self.github["runs"]

    def refresh(self, wrf):
        now = time.time()
        try:
            ours_spots = fetch("https://data.meteogust.com/runs").get("runs", {})
        except Exception:
            ours_spots = {}
        makers, maps = {}, {}

        def maker(model):
            # "done": when the run became downloadable, latest.json's write on S3 (the scheduler's latest_done: Open-Meteo's
            # last_modified_time is before the upload, ICON-EU's 120 h runs 5-20 minutes); "made": that field.
            try:
                request = urllib.request.Request(f"{BUCKET}{model}/latest.json", headers=AGENT)
                with urllib.request.urlopen(request, timeout=20) as reply:
                    latest = json.load(reply)
                    on_s3 = reply.headers.get("Last-Modified")
                made = run_unix(latest.get("last_modified_time"))
                try:
                    import email.utils
                    done = round(email.utils.parsedate_to_datetime(on_s3).timestamp())
                except (TypeError, ValueError):
                    done = made
                return {"run": run_unix(latest.get("reference_time")), "done": done, "made": made,
                        "complete": bool(latest.get("completed"))}
            except Exception:
                return None

        def our_map(set_name):
            try:
                index = fetch(f"{FILES}tiles/{set_name}/index.json?t={int(now)}")
                return {"run": run_unix(index.get("run")), "updated": run_unix(index.get("updated"))}
            except Exception:
                return None
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as pool:
            om_models = sorted({m[3] for m in MODELS if m[3]} | {"ncep_gfs025"})
            for model, value in zip(om_models, pool.map(maker, om_models)):
                makers[model] = value
                # Each complete run once, with when it became complete (latest.json's last change, whenever we look).
                if value and value["run"] and value["complete"] and (model, value["run"]) not in self.arrived:
                    self.arrived.add((model, value["run"]))
                    append(f"{STATS}/_arrivals.jsonl", {"model": model, "run": value["run"], "done": value["done"] or round(now),
                                                       "seen": round(now), "by": "panel",
                                                       **({"made": value["made"]} if value.get("made") else {})})
            sets = sorted({m[4] for m in MODELS} | {p[1] for p in PRODUCTS} | {s for p in PRODUCTS for s in p[2]})
            for set_name, value in zip(sets, pool.map(our_map, sets)):
                maps[set_name] = value
        # The makers' arrivals (the scheduler records them too; these are for models it doesn't watch).
        arrivals = {}
        for a in jsonl(f"{STATS}/_arrivals.jsonl", 4000):
            arrivals.setdefault(a["model"], []).append(a)
        gh = self.github_running()
        live = live_state()
        home_running = {r["job"]: r for r in live.get("running", [])}
        home_waiting = {w["job"]: w for w in live.get("waiting", [])}
        home_live = set(live.get("live", []))
        rows = []
        for key, label, group, om, set_name, every, (wf, word), jobs in MODELS:
            mk = makers.get(om) if om else None
            if key == "wrf_tarifa":
                mk = makers.get("ncep_gfs025")
            spots = ours_spots.get(key) or {}
            mp = maps.get(set_name) or {}
            # Ours: the spot forecasts' run where the model has them, and the map's; the older counts.
            ours_runs = [r for r in (spots.get("run"), mp.get("run")) if r]
            ours = min(ours_runs) if ours_runs else None
            published = max([t for t in (spots.get("ready"), mp.get("updated")) if t] or [0]) or None
            for kind, run, at in (("spots", spots.get("run"), spots.get("ready")), ("map", mp.get("run"), mp.get("updated"))):
                if run and self.seen.get((key, kind)) != run:
                    self.seen[(key, kind)] = run
                    append(f"{STATS}/_published.jsonl", {"key": key, "kind": kind, "run": run, "at": at or round(now), "seen": round(now)})
            # The maker's newest run at the hours our job uses (a 13Z run of a model we take every 3 h counts as 12Z).
            maker_run = maker_done = None
            if mk and mk.get("run") and mk.get("complete"):
                if key == "wrf_tarifa":
                    # WRF runs from GFS 06Z and 18Z (the Tower's 13:15 and 01:15 Riga runs, 21.10.2026).
                    maker_run = mk["run"] - mk["run"] % 3600
                    while datetime.fromtimestamp(maker_run, timezone.utc).hour % 12 != 6:
                        maker_run -= 3600
                else:
                    maker_run = mk["run"] - (mk["run"] % (every * 3600))
                same = [a for a in arrivals.get(om or "ncep_gfs025", []) if a["run"] == maker_run]
                maker_done = same[-1]["done"] if same else (mk["done"] if maker_run == mk["run"] else None)
            # Who is making the next one.
            maker_lags = [a["done"] - a["run"] for a in arrivals.get(om or "", [])][-8:]
            usual_ours = spots.get("delay")
            where = "Home" if any(j in home_live for j in jobs) or key == "wrf_tarifa" else "GitHub"
            processing = None
            for r in gh:
                if wf and r["wf"] == wf and (word is None or r["title"].split()[-1:] == [word]):
                    processing = f"{wf}{' ' + word if word else ''} {'running' if r['status'] == 'in_progress' else 'queued'} on GitHub"
            for j in jobs:
                if j in home_running and home_running[j].get("live"):
                    processing = f"{j} running at home"
                elif j in home_waiting and j in home_live and not processing:
                    processing = f"{j} queued at home: {home_waiting[j]['why']}"
            if key == "wrf_tarifa" and wrf.get("running"):
                processing = "WRF running at home" + (f", model time {wrf.get('model_time')}" if wrf.get("model_time") else "")
            behind_runs, waited = 0, None
            if maker_run and ours and maker_run > ours:
                behind_runs = int(round((maker_run - ours) / ((12 if key == "wrf_tarifa" else every) * 3600)))
                first_missing = ours + (12 if key == "wrf_tarifa" else every) * 3600
                firsts = [a["done"] for a in arrivals.get(om or "ncep_gfs025", []) if a["run"] == first_missing]
                waited = now - (firsts[-1] if firsts else (maker_done or now))
            if not maker_run:
                status = "unknown" if not ours else "ok"
            elif ours and ours >= maker_run:
                status = "ok"
            elif processing and "queued" not in processing:
                status = "proc"
            elif processing:
                status = "wait"
            else:
                allowance = ((usual_ours or 3 * 3600) - (median(maker_lags) or 0)) + 1800
                status = "bad" if waited is not None and waited > max(allowance, 1800) else "wait"
            strip = self.strip(key, om or ("ncep_gfs025" if key == "wrf_tarifa" else None), arrivals, now)
            rows.append({"key": key, "set": set_name, "label": label, "group": group, "every": every, "maker_run": maker_run, "maker_done": maker_done,
                         "ours": ours, "ours_spots": spots.get("run"), "ours_map": mp.get("run"), "published": published,
                         "status": status, "where": where, "processing": processing, "behind_runs": behind_runs, "waited": waited,
                         "usual_ours": usual_ours, "usual_maker": median(maker_lags), "strip": strip})
        products = []
        for label, set_name, inputs, job, wf in PRODUCTS:
            mp = maps.get(set_name) or {}
            newer = [s for s in inputs if (maps.get(s) or {}).get("updated") and mp.get("updated") and maps[s]["updated"] > mp["updated"]]
            running = job in home_running and home_running[job].get("live")
            gh_running = any(r["wf"] == wf for r in gh)
            products.append({"label": label, "updated": mp.get("updated"), "run": mp.get("run"), "newer_inputs": newer,
                             "processing": f"{job} running at home" if running else f"{wf} on GitHub" if gh_running else None})
        counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "proc", "wait", "bad", "unknown")}
        with self.lock:
            self.data = {"t": round(now), "rows": rows, "products": products, "counts": counts, "github_at": round(self.github["at"])}

    def strip(self, key, om, arrivals, now):
        """The last 24 h: each of the maker's runs (complete) and when ours came ([run, done, published or None])."""
        since = now - 24 * 3600
        pubs = {}
        for p in jsonl(f"{STATS}/_published.jsonl", 3000):
            if p["key"] == key:
                pubs.setdefault(p["run"], p["at"])
        out = []
        for a in arrivals.get(om or "", []):
            if a["done"] >= since:
                out.append([a["run"], a["done"], pubs.get(a["run"])])
        return out[-30:]


# MARK: Capacity

def capacity(now=None):
    now = now or time.time()
    since = now - 24 * 3600
    runs = [r for r in jsonl(f"{STATS}/_runs.jsonl", 3000) if r.get("end", 0) >= since]
    jobs = {}
    for r in runs:
        j = jobs.setdefault(r["job"], {"job": r["job"], "runs": 0, "cpu_h": 0.0, "wall_h": 0.0, "peak_mb": 0, "waited_h": 0.0,
                                       "longest_wait": 0, "failed": 0, "net_gb": 0.0})
        j["runs"] += 1
        j["wall_h"] += (r["end"] - r["start"]) / 3600
        wait = max(r["start"] - r.get("due", r["start"]), 0)
        j["waited_h"] += wait / 3600
        j["longest_wait"] = max(j["longest_wait"], wait)
        if r.get("exit") != 0:
            j["failed"] += 1
    for name, j in jobs.items():
        for s in jsonl(f"{STATS}/{name}.jsonl", 200):
            if s.get("end", 0) >= since:
                j["cpu_h"] += ((s.get("cpu_user_s") or 0) + (s.get("cpu_sys_s") or 0)) / 3600
                j["peak_mb"] = max(j["peak_mb"], s.get("peak_rss_mb") or 0)
                j["net_gb"] += (s.get("net_in_mb") or 0) / 1000
    for j in jobs.values():
        for k in ("cpu_h", "wall_h", "waited_h", "net_gb"):
            j[k] = round(j[k], 2)
    # The machine by hour (Riga): CPU of WRF and of the jobs (docker stats every minute), free memory, jobs waiting.
    from zoneinfo import ZoneInfo
    riga = ZoneInfo("Europe/Riga")
    hours = [{"h": h, "wrf": [], "jobs": [], "free": [], "waiting": 0} for h in range(24)]
    threads = os.cpu_count() or 20
    samples = [s for s in jsonl(f"{STATS}/_samples.jsonl", 2000) if s.get("t", 0) >= since]
    for s in samples:
        h = hours[datetime.fromtimestamp(s["t"], riga).hour]
        wrf = jobs_cpu = 0.0
        for c in s.get("containers") or []:
            try:
                pct = float(str(c.get("cpu") or "0").rstrip("%"))
            except ValueError:
                pct = 0
            name = c.get("name") or ""
            if name.startswith("mg-"):
                jobs_cpu += pct
            elif name != "meteogust-tunnel":
                wrf += pct
        h["wrf"].append(wrf / threads)
        h["jobs"].append(jobs_cpu / threads)
        h["free"].append(s.get("mem_free_mb") or 0)
    for r in runs:
        start, due = r["start"], r.get("due", r["start"])
        if start - due > 300:
            t = due
            while t < start:
                hours[datetime.fromtimestamp(t, riga).hour]["waiting"] += 1 / 6  # job-hours waited, in 10-minute steps
                t += 600
    by_hour = [{"h": h["h"], "wrf": round(sum(h["wrf"]) / len(h["wrf"]), 1) if h["wrf"] else None,
                "jobs": round(sum(h["jobs"]) / len(h["jobs"]), 1) if h["jobs"] else None,
                "free_min": min(h["free"]) if h["free"] else None, "waiting": round(h["waiting"], 1)} for h in hours]
    cpu_all = [w + j for h in hours for w, j in zip(h["wrf"], h["jobs"])]
    full_min = sum(1 for s in samples if (s.get("mem_free_mb") or 99999) < 1500)
    biggest = max(jobs.values(), key=lambda j: j["peak_mb"], default=None)
    return {"t": round(now), "jobs": sorted(jobs.values(), key=lambda j: -j["cpu_h"]), "by_hour": by_hour,
            "cpu_day": round(sum(cpu_all) / len(cpu_all), 1) if cpu_all else None,
            "cpu_peak": round(max(cpu_all), 1) if cpu_all else None,
            "mem_full_h": round(full_min / 60, 1), "waited_h": round(sum(j["waited_h"] for j in jobs.values()), 1),
            "long_waits": sum(1 for r in runs if r["start"] - r.get("due", r["start"]) > 900),
            "cpu_h_day": round(sum(j["cpu_h"] for j in jobs.values()), 1),
            "biggest": biggest and {"job": biggest["job"], "peak_mb": biggest["peak_mb"]},
            "samples_from": samples[0]["t"] if samples else None, "threads": threads}


# MARK: Admin

class Admin:
    def __init__(self):
        self.data = {"t": 0}

    def refresh(self):
        now = time.time()
        feeds = []

        def head(item):
            key, name, path, usual, late = item
            try:
                reply = fetch(f"{FILES}{path}?t={int(now)}", method="HEAD")
                modified = reply.headers.get("Last-Modified")
                age = now - datetime.strptime(modified, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc).timestamp()
            except Exception:
                age = None
            return {"key": key, "name": name, "usual": usual, "age": round(age) if age is not None else None,
                    "late": age is None or age > late}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as pool:
            feeds = list(pool.map(head, WATCHED))
        try:
            beat = fetch(f"{FILES}runner/heartbeat.json?t={int(now)}")
            age = now - beat["t"]
            feeds.append({"key": "runner", "name": "Home runner (heartbeat)", "usual": "5 min", "age": round(age), "late": age > 1200})
        except Exception:
            pass
        overview, error = None, None
        cid, secret = env_value("SHOREWIND_ADMIN_ID"), env_value("SHOREWIND_ADMIN_SECRET")
        if cid and secret:
            try:
                overview = fetch("https://admin.meteogust.com/api/overview", {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret},
                                 timeout=40)
            except Exception as e:
                error = f"admin.meteogust.com not read ({type(e).__name__})"
        else:
            error = "token"
        self.data = {"t": round(now), "feeds": feeds, "overview": overview, "error": error}
