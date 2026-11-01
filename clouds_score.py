"""The satellite clouds' scorecard (the owner, 01.11.2026: "Doesn't this mean we fix one and brake other?" after the
night and dawn rules for the deck EUMETSAT's Cloud Mask missed; "Yes, build it"): every published frame at :15 and :45
(tiles/sat/index.json) against the METARs of 20 airports round the Baltic at :20 and :50, by the sun's height at
each airport: night (under -6 deg), low sun (-6 to 15: dawn, dusk and November's whole day) and day. Per UTC day and
band: overcast reports (BKN, OVC, VV), the ones drawn clear, clear reports (CAVOK, NSC, NCD, SKC, CLR, FEW only) and
the ones drawn cloud. A frame's cloud at an airport is its zoom-6 tile's alpha (a full deck 216) over 3 x 3 pixels
(about 4 km), cloud from CUT. Only the tiles the airports are in are read (Range requests), not the 7 MB packs.

Kept in runner/clouds-score.json (the last 30 days; each frame scored once, 90 minutes after its time, as published
then) with `flags`: yesterday's bands off their last two weeks (at least 20 reports each), read by the ops panel.

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
CUT = 108  # half a full deck's alpha
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
    """Each point's cloud (alpha 0-255, 3 x 3 mean) in one published pack; no tile there: clear (0)."""
    url = f"{FILES}tiles/sat/{file}"
    n = struct.unpack("<I", get(url, {"Range": "bytes=0-7"})[4:8])[0]
    tiles = json.loads(get(url, {"Range": f"bytes=8-{7 + n}"}))["tiles"]
    alpha = {}
    for key in {place(*p)[0] for p in points}:
        if key in tiles:
            o, k = tiles[key]
            png = get(url, {"Range": f"bytes={8 + n + o}-{8 + n + o + k - 1}"})
            alpha[key] = np.asarray(Image.open(io.BytesIO(png)).convert("LA"))[..., 1].astype(np.float32)
    out = []
    for p in points:
        key, x, y = place(*p)
        a = alpha.get(key)
        out.append(0.0 if a is None else float(a[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2].mean()))
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
        cloud = covers(frame["file"], [(lat, lon) for lat, lon, _, _ in found]) if found else []
        return t, [(kind, b, c >= CUT) for (_, _, kind, b), c in zip(found, cloud)]

    days = state.setdefault("days", {})
    with ThreadPoolExecutor(6) as pool:
        for t, rows in pool.map(one, frames):
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
    for old in sorted(days)[:-KEEP_DAYS]:
        del days[old]
    state["flags"] = flags(days)
    state["updated"] = round(now)
    return len(frames)


def check():
    assert sky("EVRA 010720Z 18005KT 9999 OVC011 06/05 Q1021") == "cloudy"
    assert sky("EVRA 010720Z 18005KT CAVOK 06/05 Q1021") == "clear"
    assert sky("EVRA 010720Z 9999 FEW020 SCT040 06/05 Q1021") is None
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
