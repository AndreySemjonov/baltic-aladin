"""Kolka's station as ours (the owner, 25.10.2026: "We can make on tower now fetching Kolka", "Go with plan A"):
meteo.kolka.lv publishes only two pictures (wind1.png: Vidēji = the average, Nesenas brāzmas = the recent gust, the
direction; lcd_pagasts.png: the temperature), read digit by digit with the NAS's own kolka.py (beside this file, as the
station owner's uploader has it; its Windguru upload is not used here). The newest reading goes to station-history as
home-stations/kolka.json, which its 5-minute collect adds as the station lv-kolka (homeStationRows):

    {"t": the wind picture's time (unix), "wind": average m/s, "gust": recent gust m/s, "direction": degrees,
     "temperature": °C or null}

The real average and gust, not the Windguru upload's mapping (the average as its minimum, the midpoint as its
average). A picture over 20 minutes old or a digit kolka.py isn't sure of is no reading: the job fails, nothing goes up.
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


def reading():
    png, modified = kolka.fetch_image(kolka.IMAGE_URL, "wind1.png", 20)
    wind = kolka.extract_wind(png)
    t = parsedate_to_datetime(modified).timestamp() if modified else time.time()
    try:
        temperature_png, _ = kolka.fetch_image(kolka.TEMPERATURE_IMAGE_URL, "lcd_pagasts.png", 20)
        temperature = kolka.extract_temperature(temperature_png)[0]
    except Exception as error:
        print(f"temperature left out: {error}", file=sys.stderr)
        temperature = None
    return {"t": round(t), "wind": wind["average_ms"], "gust": wind["recent_gust_ms"],
            "direction": wind["direction_degrees"], "temperature": temperature, "source": "meteo.kolka.lv"}


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
