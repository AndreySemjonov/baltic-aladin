"""The satellite clouds' scorecard (the owner, 01.11.2026: "Doesn't this mean we fix one and brake other?" after the
night and dawn rules for the deck EUMETSAT's Cloud Mask missed; "Yes, build it"): every published frame at :15 and :45
(tiles/sat/index.json) against the METARs of 20 airports round the Baltic at :20 and :50, by the sun's height at
each airport: night (under -6 deg), low sun (-6 to 15: dawn, dusk and November's whole day) and day. Per UTC day and
band: overcast reports (BKN, OVC, VV), the ones drawn clear, clear reports (CAVOK, NSC, NCD, SKC, CLR, FEW only) and
the ones drawn cloud. A frame's cloud at an airport is the share of its zoom-6 tile's pixels within ~5 km (9 x 9) with
alpha (a full deck 216) from CUT: overcast drawn cloud from a quarter, clear drawn cloud from three quarters (since
06.11.2026, version 3: in a field of cells the 3 x 3 point over the airport sat in gaps the map had about right, the
owner, "lets investigate", Riga BKN014 under a clear point with half its ring cloud). A clear report under drawn
cloud topped above HIGH_KM (the tile's grey, clouds.HEIGHT_GREY) is no verdict (since 12.11.2026, version 4). Only the tiles the airports are in are read (Range requests), not the 7 MB packs.

Kept in runner/clouds-score.json (the last 30 days; each frame scored once, 90 minutes after its time, as published
then) with `flags`: yesterday's bands off their last two weeks (at least 20 reports each), and the last 48 hours' frames
airport by airport (`frames`: time, the band at Riga, one letter an airport in `stations`' order, CELLS), read by the ops
panel's Rain & clouds tab (the owner, 01.11.2026: "make same in ops, but in separate tab - rain and clouds. For clouds
lets make also graphical representation how our clouds correct").

    python clouds_score.py              # score the frames since the last run, upload (INGEST_TOKEN)
    python clouds_score.py --local DIR  # the runner's shadow: DIR/clouds-score.json, nothing uploaded
"""
import csv
import io
import json
import math
import os
import statistics
import struct
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from clouds import sun_height

FILES = "https://files.meteogust.com/"
UPLOAD = os.environ.get("SCORE_UPLOAD", "https://data.meteogust.com/tiles/upload/runner/clouds-score.json")
AGENT = {"User-Agent": "meteogust-helper"}
STATIONS = ["EVRA", "EVLA", "EVVA", "EYSA", "EYKA", "EYVI", "EYPA", "EETN", "EETU", "EEPU", "EEKE", "EFHK", "EFTU",
            "ESSA", "ESSV", "EPGD", "UMKK", "ESMQ", "EYPP", "EFHA"]
NAMES = {"EVRA": "Riga", "EVLA": "Liepāja", "EVVA": "Ventspils", "EYSA": "Šiauliai", "EYKA": "Kaunas", "EYVI": "Vilnius",
         "EYPA": "Palanga", "EETN": "Tallinn", "EETU": "Tartu", "EEPU": "Pärnu", "EEKE": "Kuressaare", "EFHK": "Helsinki",
         "EFTU": "Turku", "ESSA": "Stockholm", "ESSV": "Visby", "EPGD": "Gdańsk", "UMKK": "Kaliningrad", "ESMQ": "Kalmar",
         "EYPP": "Panevėžys", "EFHA": "Halli"}
CUT = 108  # half a full deck's alpha
NEAR = 4  # pixels each way: 9 x 9, about 5 km at zoom 6
# A clear report (CAVOK, NSC, FEW: the sky below ~1.5 km, or the ceilometer's reach) can't judge a deck topped higher:
# 11.11.2026's frontal night scored 32 of 33 clear reports drawn cloud, 7 of the 9 still in the index topped 3-8 km
# (the owner, 12.11.2026: "Yes, do B"). Not lower: a stratocumulus deck tops out near 2-3 km with its base under 1.5 km.
HIGH_KM = 3
BANDS = ("night", "low", "day")
# A frame's letter at an airport: "." no verdict (no report, SCT); else "a" + 4 x its band + overcast drawn cloud 0,
# overcast drawn clear 1, clear drawn clear 2, clear drawn cloud 3.
CELLS = 48 * 3600
ZOOM = 6
KEEP_DAYS = 30


def get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers={**AGENT, **(headers or {})}), timeout=60) as reply:
        return reply.read()


def sky(metar):
    """'cloudy' (BKN/OVC/VV), 'clear' (CAVOK/NSC/NCD/SKC/CLR or only FEW), else None (SCT, or no cloud group)."""
    import re
    if re.search(r"\b(BKN|OVC)\d{3}|\bVV(\d{3}|///)", metar):
        return "cloudy"
    if re.search(r"\bSCT\d{3}", metar):
        return None
    if re.search(r"\b(CAVOK|NSC|NCD|SKC|CLR)\b|\bFEW\d{3}", metar):
        return "clear"
    return None


def cloud_near(kind, share):
    """Drawn cloud at an airport: for an overcast report a quarter of the ~5 km round it cloudy, for a clear one three
    quarters (a cell gap or a cell next door is no miss either way)."""
    return share >= (0.25 if kind == "cloudy" else 0.75)


def judged(kind, share, top_km):
    """False where the report can't judge what's drawn: clear under cloud topped above HIGH_KM."""
    return not (kind == "clear" and cloud_near(kind, share) and top_km > HIGH_KM)


def top_km(grey):
    """The tile's grey back to the cloud top's height (km), as clouds.style_height drew it (its texture evened out by a
    median)."""
    return float(np.interp(grey, [128, 144, 198, 224, 229], [0, 2, 6, 10, 12]))


def band(sun):
    return "night" if sun < -6 else "low" if sun < 15 else "day"


def metars(start, end):
    """{(station, time): (lat, lon, metar)} from Iowa State's archive (routine and special reports)."""
    q = "&".join(f"station={s}" for s in STATIONS)
    url = (f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?{q}&data=metar&year1={start:%Y}&month1={start.month}"
           f"&day1={start.day}&year2={end:%Y}&month2={end.month}&day2={end.day}&tz=Etc/UTC&format=onlycomma&latlon=yes"
           f"&report_type=3&report_type=4")
    rows = {}
    for r in csv.DictReader(io.StringIO(get(url).decode("utf-8", "replace"))):
        try:
            t = datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            rows[(r["station"], t)] = (float(r["lat"]), float(r["lon"]), r["metar"])
        except (KeyError, ValueError):
            pass
    return rows


def place(lat, lon):
    """The zoom-6 tile key and the pixel in it."""
    n = (1 << ZOOM) * 256
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return f"{ZOOM}/{int(x // 256)}/{int(y // 256)}", int(x % 256), int(y % 256)


def covers(file, points):
    """Each point's (share of cloudy pixels (alpha from CUT) within NEAR, the cloudy ones' median top in km) in one
    published pack; no tile there: (0, 0)."""
    url = f"{FILES}tiles/sat/{file}"
    n = struct.unpack("<I", get(url, {"Range": "bytes=0-7"})[4:8])[0]
    tiles = json.loads(get(url, {"Range": f"bytes=8-{7 + n}"}))["tiles"]
    la = {}
    for key in {place(*p)[0] for p in points}:
        if key in tiles:
            o, k = tiles[key]
            png = get(url, {"Range": f"bytes={8 + n + o}-{8 + n + o + k - 1}"})
            la[key] = np.asarray(Image.open(io.BytesIO(png)).convert("LA")).astype(np.float32)
    out = []
    for p in points:
        key, x, y = place(*p)
        a = la.get(key)
        if a is None:
            out.append((0.0, 0.0))
            continue
        # shortcut: the window stops at the tile's edge (a smaller one there), fine while no airport sits on an edge.
        w = a[max(y - NEAR, 0):y + NEAR + 1, max(x - NEAR, 0):x + NEAR + 1]
        cloudy = w[..., 1] >= CUT
        out.append((float(cloudy.mean()), top_km(float(np.median(w[..., 0][cloudy]))) if cloudy.any() else 0.0))
    return out


def flags(days):
    """Yesterday's bands off their last two weeks: a share over twice its median and 10 points above it."""
    names = sorted(days)
    if len(names) < 2:
        return []
    out = []
    yesterday, before = names[-2], names[-16:-2]
    for b in ("night", "low", "day"):
        for what, total, bad in (("overcast drawn clear", 0, 1), ("clear drawn cloud", 2, 3)):
            share = lambda d: days[d][b][bad] / days[d][b][total] if days[d].get(b) and days[d][b][total] >= 20 else None
            now = share(yesterday)
            usual = [s for s in map(share, before) if s is not None]
            if now is None or len(usual) < 5:
                continue
            median = statistics.median(usual)
            if now > 2 * median and now > median + 0.10:
                out.append({"day": yesterday, "band": b, "what": what, "share": round(now, 3), "usual": round(median, 3)})
    return out


def score(state, now):
    index = json.loads(get(f"{FILES}tiles/sat/index.json?t={int(now)}"))
    last = state.get("last", 0)
    frames = [f for f in index.get("frames", []) if f["time"] > last and f["time"] < now - 5400
              and datetime.fromtimestamp(f["time"], timezone.utc).minute in (15, 45)]
    if not frames:
        return 0
    start = datetime.fromtimestamp(frames[0]["time"], timezone.utc)
    reports = metars(start, datetime.fromtimestamp(now, timezone.utc) + timedelta(days=1))

    def one(frame):
        t = datetime.fromtimestamp(frame["time"], timezone.utc)
        found = []
        for st in STATIONS:
            r = next((reports[(st, t + timedelta(minutes=k))] for k in (5, 0, 10) if (st, t + timedelta(minutes=k)) in reports), None)
            if r and sky(r[2]):
                found.append((r[0], r[1], sky(r[2]), band(float(sun_height(r[0], r[1], t)))))
            else:
                found.append(None)
            if r:
                places[st] = r[:2]
        known = [x for x in found if x]
        cloud = iter(covers(frame["file"], [(lat, lon) for lat, lon, _, _ in known]) if known else [])
        rows = []
        for x in found:
            share, top = next(cloud) if x else (0.0, 0.0)
            rows.append(None if x is None or not judged(x[2], share, top) else (x[2], x[3], cloud_near(x[2], share)))
        cells = "".join("." if r is None else chr(97 + 4 * BANDS.index(r[1]) + (0 if r[0] == "cloudy" else 2) + (r[2] != (r[0] == "cloudy")))
                        for r in rows)
        return t, [r for r in rows if r], cells

    days = state.setdefault("days", {})
    kept = state.setdefault("frames", [])
    places = {}
    with ThreadPoolExecutor(6) as pool:
        for t, rows, cells in pool.map(one, frames):
            kept.append([round(t.timestamp()), band(float(sun_height(56.95, 24.1, t))), cells])
            day = days.setdefault(f"{t:%Y-%m-%d}", {})
            for kind, b, drawn in rows:
                counts = day.setdefault(b, [0, 0, 0, 0])  # overcast, drawn clear, clear, drawn cloud
                if kind == "cloudy":
                    counts[0] += 1
                    counts[1] += not drawn
                else:
                    counts[2] += 1
                    counts[3] += drawn
    state["last"] = max(f["time"] for f in frames)
    state["frames"] = [f for f in kept if f[0] > state["last"] - CELLS]
    old = {s[0]: s for s in state.get("stations", [])}
    state["stations"] = [[st, NAMES[st], round(places[st][0], 2) if st in places else (old.get(st) or [0, 0, None])[2]] for st in STATIONS]
    for old in sorted(days)[:-KEEP_DAYS]:
        del days[old]
    state["flags"] = flags(days)
    state["updated"] = round(now)
    return len(frames)


def check():
    assert sky("EVRA 010720Z 18005KT 9999 OVC011 06/05 Q1021") == "cloudy"
    assert sky("EVRA 010720Z 18005KT CAVOK 06/05 Q1021") == "clear"
    assert sky("EVRA 010720Z 9999 FEW020 SCT040 06/05 Q1021") is None
    assert cloud_near("cloudy", 0.3) and not cloud_near("cloudy", 0.2) and not cloud_near("clear", 0.5) and cloud_near("clear", 0.8)
    assert judged("clear", 0.8, 2.5) and not judged("clear", 0.8, 4) and judged("clear", 0.5, 8) and judged("cloudy", 1, 8)
    assert top_km(128) == 0 and abs(top_km(157.5) - 3) < 0.01
    assert band(-7) == "night" and band(-6) == "low" and band(15) == "day"
    days = {f"2026-10-{d:02d}": {"night": [100, 10, 50, 1]} for d in range(10, 25)}
    days["2026-10-25"] = {"night": [100, 30, 50, 1]}
    days["2026-10-26"] = {}
    assert [f["what"] for f in flags(days)] == ["overcast drawn clear"], flags(days)
    days["2026-10-25"]["night"][1] = 15
    assert flags(days) == []


if __name__ == "__main__":
    check()
    local = Path(sys.argv[sys.argv.index("--local") + 1]) if "--local" in sys.argv else None
    try:
        state = json.loads(get(f"{FILES}runner/clouds-score.json?t={int(time.time())}"))
    except Exception:
        state = {}
    if state.get("version") == 3:
        # 4: no verdict for clear under high cloud (12.11.2026). The days kept: their clear counts a bit high till they
        # age out (two weeks), rather than no flags at all while new ones gather.
        state["version"] = 4
    if state.get("version") != 4:
        # 2: with the frames airport by airport (01.11.2026); 3: cloud within ~5 km (06.11.2026). The index's day scored again.
        state = {"version": 4}
    made = score(state, time.time())
    body = json.dumps(state, separators=(",", ":")).encode()
    print(f"{made} frames scored, {len(body)} bytes, flags: {state.get('flags', [])}")
    for day, bands in sorted(state.get("days", {}).items())[-3:]:
        print(" ", day, bands)
    if local:
        local.mkdir(parents=True, exist_ok=True)
        (local / "clouds-score.json").write_bytes(body)
    elif made:
        token = os.environ.get("INGEST_TOKEN")
        if not token:
            sys.exit("INGEST_TOKEN missing: nothing sent")
        request = urllib.request.Request(UPLOAD, data=body, method="PUT", headers={
            **AGENT, "Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as reply:
            reply.read()
