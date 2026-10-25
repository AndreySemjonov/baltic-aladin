"""Kolka's station as ours (the owner, 25.10.2026: "We can make on tower now fetching Kolka", "Go with plan A"):
meteo.kolka.lv publishes only two pictures (wind1.png: Vidēji = the average, Nesenas brāzmas = the recent gust, the
direction; lcd_pagasts.png: the temperature), read digit by digit with the NAS's own kolka.py (beside this file, as the
station owner's uploader has it; its Windguru upload is not used here). The newest reading goes to station-history as
home-stations/kolka.json, which its 5-minute collect adds as the station lv-kolka (homeStationRows):

    {"t": the wind picture's time (unix), "wind": average m/s, "gust": recent gust m/s, "direction": degrees,
     "temperature": °C or null, "videji": the station's own average m/s}

The average is the midpoint of Vidēji and the recent gust, as the NAS sends Windguru (the station owner's mapping; the
owner, 25.10.2026: "Keep B"): Vidēji alone read 0.0-2.1 m/s beside 4.1-5.4 m/s gusts that morning (04:01 UTC: 0.0,
gust 4.1, Beaufort "0: Bezvējš"), a gust factor of 2.5 and more where the coast's run 1.3-1.6 (Daugavgrīva 3.7 gusting
5.2 then): most likely a much shorter window than the gust's. Vidēji kept beside it in the file ("videji") to compare.
A picture over 20 minutes old or a digit kolka.py isn't sure of is no reading: the job fails, nothing goes up.
Run by the home runner (runner/jobs.py "kolka").

    python stations/kolka_home.py [--local DIR]     DIR/home-stations/kolka.json instead of the upload (shadow, a test)
"""
import json
import os
import sys
import time
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kolka  # noqa: E402

UPLOAD = "https://data.meteogust.com/tiles/upload/home-stations/kolka.json"
# The file store's protection turns Python's own user agent away (maplib.USER_AGENT's).
AGENT = "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"


def picture(url):
    """(the PNG, its time on our clock). The station's server clock runs ahead (2 minutes on 25.10.2026: Date 03:50:38
    at 03:48:40 UTC), so its Last-Modified as such would put readings in the future and skew kolka.fetch_image's age
    check: the picture's age on its own clock (Date - Last-Modified) instead, from our now."""
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=20) as response:
        if "image/png" not in response.headers.get("Content-Type", "").lower():
            raise RuntimeError(f"{url}: not a PNG")
        data = response.read()
        served, modified = response.headers.get("Date"), response.headers.get("Last-Modified")
    age = 0.0
    if served and modified:
        age = max(parsedate_to_datetime(served).timestamp() - parsedate_to_datetime(modified).timestamp(), 0.0)
    if age > 20 * 60:
        raise RuntimeError(f"{url} is stale: {age / 60:.0f} minutes old")
    return data, time.time() - age


def reading():
    png, t = picture(kolka.IMAGE_URL)
    wind = kolka.extract_wind(png)
    try:
        temperature_png, _ = picture(kolka.TEMPERATURE_IMAGE_URL)
        temperature = kolka.extract_temperature(temperature_png)[0]
    except Exception as error:
        print(f"temperature left out: {error}", file=sys.stderr)
        temperature = None
    average = (wind["average_ms"] + wind["recent_gust_ms"]) / 2
    return {"t": round(t), "wind": round(average, 2), "gust": wind["recent_gust_ms"],
            "direction": wind["direction_degrees"], "temperature": temperature, "videji": wind["average_ms"],
            "source": "meteo.kolka.lv"}


def main(args):
    body = reading()
    print(json.dumps(body))
    data = json.dumps(body, separators=(",", ":")).encode()
    if "--local" in args:
        out = Path(args[args.index("--local") + 1]) / "home-stations"
        out.mkdir(parents=True, exist_ok=True)
        (out / "kolka.json").write_bytes(data)
        return
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        raise SystemExit("INGEST_TOKEN is not set")
    request = urllib.request.Request(UPLOAD, data=data, method="PUT",
                                     headers={"Authorization": "Bearer " + token, "User-Agent": AGENT,
                                              "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as reply:
        print("uploaded:", reply.read().decode().strip())


if __name__ == "__main__":
    main(sys.argv[1:])
