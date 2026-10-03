"""The coastal water level gauges of Estonia and Latvia in one file, docs/gauges.json: each
gauge's place and its last 48 hours, level in cm on one height system (EH2000 = LAS-2000,5,
both on EVRF2007) and water temperature. For the app's gauge pins on the water map and for
nudging the water map towards the measurements.

Estonia: the Estonian Environment Agency, read from the gauges' pages (ee_gauges.py).
Latvia: LVĢMC's hourly hydrological data on data.gov.lv (CC0). LVĢMC counts each gauge from
its own zero (level = SEHL - 500 cm); the corrections below put them on LAS-2000,5, found on
2.10.2026 against the Estonian gauges and the sea models (the app uses the same).
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUTPUT = HERE / "docs" / "gauges.json"
USER_AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"
LATVIA_DATA = "https://data.gov.lv/dati/api/3/action/datastore_search"
LATVIA_RESOURCE = "de5f06e9-6f44-497d-8ec2-72a2483608e8"

# name -> (latitude, longitude), as in the agency's observations file.
ESTONIA = {
    "Häädemeeste": (58.0367, 24.4581), "Pärnu": (58.3846, 24.4852), "Mõntu": (57.9494, 22.1242),
    "Ruhnu": (57.7834, 23.2589), "Virtsu": (58.5727, 23.5136), "Roomassaare": (58.2181, 22.5064),
    "Ristna": (58.9210, 22.0663), "Heltermaa": (58.8642, 23.0437), "Haapsalu sadam": (58.9580, 23.5273),
    "Dirhami": (59.2114, 23.5008), "Paldiski (Põhjasadam)": (59.3497, 24.0475), "Pirita": (59.4688, 24.8208),
    "Loksa": (59.5833, 25.6986), "Kunda": (59.5214, 26.5414), "Narva-Jõesuu": (59.4683, 28.0425),
}
# id -> (name, latitude, longitude, correction cm).
LATVIA = {
    "RIKO99PA": ("Kolka", 57.7470, 22.5878, 18), "RILP99PA": ("Liepāja", 56.4754, 21.0206, 17),
    "RIME99MS": ("Mērsrags", 57.3333, 23.1131, 8), "RISE99MS": ("Skulte", 57.3006, 24.4122, 17),
    "RIVE99PA": ("Ventspils", 57.3956, 21.5372, 12), "SALACGRI": ("Salacgrīva", 57.7542, 24.3508, 7),
    "SEDA99MS": ("Daugavgrīva", 57.0592, 24.0233, 17), "SEJU99MS": ("Lielupes grīva", 56.9833, 23.8875, 6),
    "SERO99MS": ("Roja", 57.5067, 22.8014, 8),
}


def latvia(gauge, correction):
    """{unix hour: [level, temperature]} for one Latvian gauge, its last 48 hours."""
    query = urllib.parse.urlencode({"resource_id": LATVIA_RESOURCE, "filters": json.dumps({"STATION_ID": gauge}),
                                    "limit": "500"})
    request = urllib.request.Request(f"{LATVIA_DATA}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as reply:
        records = json.load(reply)["result"]["records"]
    hours = {}
    for record in records:
        if record.get("VALUE") is None:
            continue
        t = int(datetime.fromisoformat(record["DATETIME"]).replace(tzinfo=timezone.utc).timestamp())
        if record["ABBREVIATION"] == "SEHL":
            hours.setdefault(t, [None, None])[0] = record["VALUE"] - 500 + correction
        elif record["ABBREVIATION"] == "SEDUT":
            hours.setdefault(t, [None, None])[1] = record["VALUE"]
    return hours


def series(hours):
    first, last = min(hours), max(hours)
    times = range(first, last + 1, 3600)
    return {"start": first, "step": 3600,
            "level": [hours.get(t, [None, None])[0] for t in times],
            "temperature": [hours.get(t, [None, None])[1] for t in times]}


def write(estonia, now):
    """All gauges into docs/gauges.json: Estonia's from `estonia` (ee-gauges' "gauges"), Latvia's read now."""
    gauges = []
    for name, (lat, lon) in ESTONIA.items():
        if name in estonia:
            gauges.append({"id": name, "name": name.replace(" sadam", "").split(" (")[0], "country": "EE",
                           "lat": lat, "lon": lon, **estonia[name]})
    for gauge, (name, lat, lon, correction) in LATVIA.items():
        try:
            hours = latvia(gauge, correction)
        except Exception as error:  # a gauge failing is left out this time
            print(f"{name}: {error}")
            continue
        if hours:
            gauges.append({"id": gauge, "name": name, "country": "LV", "lat": lat, "lon": lon, "correction": correction,
                           **series(hours)})
    OUTPUT.write_text(json.dumps({
        "source": "Estonian Environment Agency (ilmateenistus.ee) and LVĢMC (data.gov.lv, CC0)",
        "made": now.strftime("%Y-%m-%dT%H:%MZ"),
        "units": {"level": "cm, EH2000 / LAS-2000,5 (Latvian gauges corrected from their own zero)",
                  "temperature": "degC"},
        "gauges": gauges}, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Gauges: {len(gauges)} in gauges.json")


def load():
    """docs/gauges.json's gauges with {unix hour: level} added as "levels"."""
    try:
        gauges = json.loads(OUTPUT.read_text(encoding="utf-8"))["gauges"]
    except (OSError, ValueError, KeyError):
        return []
    for gauge in gauges:
        gauge["levels"] = {int(gauge["start"] + k * gauge["step"]): v for k, v in enumerate(gauge["level"]) if v is not None}
    return gauges
