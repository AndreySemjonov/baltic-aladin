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

# [app key, label, group, Open-Meteo's model (None: not in its record), our set, every hours, GitHub workflow and
#  title word, the runner's jobs]
MODELS = [
    ("metno_nordic", "MET Nordic 1 km", "Fine models, Europe", "metno_nordic_pp", "metnordic-1km", 1, ("nordic.yml", None), ["nordic-1km"]),
    ("knmi_harmonie_arome_europe", "HARM-EU (KNMI)", "Fine models, Europe", "knmi_harmonie_arome_europe", "knmi-eu", 1, ("bucket.yml", None), ["bucket-knmi-eu"]),
    ("knmi_harmonie_arome_netherlands", "HARM-NL", "Fine models, Europe", "knmi_harmonie_arome_netherlands", "harm-nl", 1, ("regional.yml", None), ["regional-wind"]),
    ("icon_d2", "ICON-D2", "Fine models, Europe", "dwd_icon_d2", "icon-d2", 3, ("regional.yml", None), ["regional-wind"]),
    ("meteofrance_arome_france_hd", "AROME 1.3 km", "Fine models, Europe", "meteofrance_arome_france_hd", "arome", 3, ("regional.yml", None), ["regional-wind"]),
    ("dmi_harmonie_arome_europe", "HARM-DK (DINI)", "Fine models, Europe", "dmi_harmonie_arome_europe", "dini", 3, ("regional.yml", None), ["regional-wind"]),
    ("meteoswiss_icon_ch1", "ICON-CH1", "Fine models, Europe", "meteoswiss_icon_ch1", "icon-ch1", 3, ("regional.yml", None), ["regional-wind"]),
    ("italia_meteo_arpae_icon_2i", "ICON-2I", "Fine models, Europe", "italia_meteo_arpae_icon_2i", "icon-2i", 12, ("regional.yml", None), ["regional-wind"]),
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


# MARK: Freshness

class Freshness:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}
        self.github = {"at": 0, "runs": []}
        self.seen = {}  # (key, kind) -> run, to record our published runs once
        for p in jsonl(f"{STATS}/_published.jsonl", 3000):
            self.seen[(p["key"], p["kind"])] = p["run"]

    def github_running(self):
        """GitHub's runs in progress or queued (public API, every 2 minutes)."""
        if time.time() - self.github["at"] > 120:
            runs = []
            for status in ("in_progress", "queued"):
                try:
                    reply = fetch(f"{GITHUB_RUNS}?status={status}&per_page=50")
                    runs += [{"wf": os.path.basename(r.get("path", "")), "title": r.get("display_title") or "",
                              "started": run_unix(r.get("run_started_at")), "status": status} for r in reply.get("workflow_runs", [])]
                except Exception:
                    pass
            self.github = {"at": time.time(), "runs": runs}
        return self.github["runs"]

    def refresh(self, wrf):
        now = time.time()
        try:
            ours_spots = fetch("https://data.meteogust.com/runs").get("runs", {})
        except Exception:
            ours_spots = {}
        makers, maps = {}, {}

        def maker(model):
            try:
                latest = fetch(f"{BUCKET}{model}/latest.json")
                return {"run": run_unix(latest.get("reference_time")), "done": run_unix(latest.get("last_modified_time")),
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
            rows.append({"key": key, "label": label, "group": group, "every": every, "maker_run": maker_run, "maker_done": maker_done,
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
