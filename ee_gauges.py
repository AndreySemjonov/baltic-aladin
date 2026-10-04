"""The last 7 days of the Estonian coastal gauges: sea level (EH2000, cm) and water
temperature (°C), hour by hour, from each gauge's page on ilmateenistus.ee (Estonian
Environment Agency), whose charts carry 10 days. Each run adds to the hours it kept, so a
page that fails keeps its history. Writes docs/ee-gauges.json, at most once an hour (the
pages change hourly).

    python ee_gauges.py           # only if the file is older than 50 minutes
    python ee_gauges.py --force
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import gauges as all_gauges

HERE = Path(__file__).parent
OUTPUT = HERE / "docs" / "ee-gauges.json"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"
PAGE = "https://www.ilmateenistus.ee/meri/vaatlusandmed/{}/"
TALLINN = ZoneInfo("Europe/Tallinn")
KEEP_HOURS = 7 * 24
# The site answers "429 Too Many Requests" after a handful of quick page loads: one page
# every 10 seconds, and on a 429 the rest keep their last hours until the next run.
PAUSE = 10
# The gauge's name in the agency's observations file -> its page.
GAUGES = {
    # Gulf of Riga first: these are next to the spots.
    "Häädemeeste": "haademeeste", "Pärnu": "parnu-sadam", "Ruhnu": "ruhnu", "Mõntu": "montu",
    "Virtsu": "virtsu", "Roomassaare": "roomassaare", "Ristna": "ristna-2", "Heltermaa": "heltermaa",
    "Haapsalu sadam": "haapsalu-sadam", "Dirhami": "dirhami", "Paldiski (Põhjasadam)": "paldiski-pohjasadam",
    "Pirita": "pirita", "Loksa": "loksa", "Kunda": "kunda", "Narva-Jõesuu": "narva-joesuu",
}


def chart_hours(html, now):
    """{unix hour: [level, temperature]} from the page's chart data."""
    start = html.find('_var["chartConfig"]')
    if start < 0:
        return {}
    charts, _ = json.JSONDecoder().raw_decode(html[html.index("{", start):])

    def series(key, name=None):
        chart = charts.get(key) or {}
        for line in chart.get("y_axis") or []:
            if name is None or name in (line.get("name") or ""):
                return chart.get("x_axis") or [], line.get("data") or []
        return [], []

    def when(label):
        digits = [int(d) for d in re.findall(r"\d+", re.sub(r"<[^>]+>", "", label))]
        if len(digits) != 4:
            return None
        day, month, hour, minute = digits
        for year in (now.year, now.year - 1):
            local = datetime(year, month, day, hour, minute, tzinfo=TALLINN)
            if local.timestamp() <= now.timestamp() + 3600:
                return int(local.timestamp())
        return None

    hours = {}
    for index, (key, name) in enumerate((("water_level", "EH2000"), ("water_temp", None))):
        labels, values = series(key, name)
        for label, value in zip(labels, values):
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            t = when(label)
            if t is not None:
                hours.setdefault(t, [None, None])[index] = number
    return hours


def main():
    now = datetime.now(timezone.utc)
    try:
        old = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    made = old.get("made")
    if made and "--force" not in sys.argv:
        age = now.timestamp() - datetime.strptime(made, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp()
        if age < 50 * 60:
            print(f"Estonian gauges read {int(age / 60)} min ago")
            return
    cutoff = now.timestamp() - KEEP_HOURS * 3600
    gauges, failed = {}, []
    slowed = False
    for index, (name, page) in enumerate(GAUGES.items()):
        hours = {}
        if not slowed:
            if index:
                time.sleep(PAUSE)
            try:
                request = urllib.request.Request(PAGE.format(page), headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=60) as reply:
                    hours = chart_hours(reply.read().decode("utf-8", "replace"), now)
            except urllib.error.HTTPError as error:
                print(f"{name}: {error}")
                slowed = error.code == 429
            except Exception as error:  # one gauge failing keeps its last hours
                print(f"{name}: {error}")
        if not hours:
            failed.append(name)
        # The hours kept from earlier runs, the page's hours over them.
        kept = all_gauges.hours_of((old.get("gauges") or {}).get(name))
        for t, (level, temperature) in hours.items():
            values = kept.setdefault(t, [None, None])
            if level is not None:
                values[0] = level
            if temperature is not None:
                values[1] = temperature
        hours = {t: v for t, v in kept.items() if t >= cutoff}
        if not hours:
            continue
        first, last = min(hours), max(hours)
        times = range(first, last + 1, 3600)
        gauges[name] = {"start": first, "step": 3600,
                        "level": [hours.get(t, [None, None])[0] for t in times],
                        "temperature": [hours.get(t, [None, None])[1] for t in times]}
    out = {"source": "Estonian Environment Agency (Keskkonnaagentuur), ilmateenistus.ee",
           "made": now.strftime("%Y-%m-%dT%H:%MZ"),
           "units": {"level": "cm, EH2000", "temperature": "degC"},
           "gauges": gauges, "failed": failed}
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    # With the Latvian gauges, all in one file (the app's gauge pins, the water map's nudge).
    all_gauges.write(gauges, now)
    newest = max((g["start"] + 3600 * (len(g["level"]) - 1) for g in gauges.values()), default=None)
    print(f"Estonian gauges: {len(gauges)} read, failed {failed}, newest hour "
          f"{datetime.fromtimestamp(newest, timezone.utc):%Y-%m-%d %H:%M}Z" if newest else "Estonian gauges: none")


if __name__ == "__main__":
    main()
