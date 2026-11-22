"""The tide at a place and moment, as the server's /sea and the apps predict it (station-history sea.js worldTide,
WindCore HarmonicTide): EOT20's constituents from our packs (tides_world.py: tiles/tides/5-x-y.pack, the nearest three
cells within 0.6 degrees blended as complex harmonics), h = sum of f A cos(G + u - phase) with pyTMD's arguments (FES
corrections, as the packs' table), for any moment, not only the table's 2026-2036: the survey's scenes since 2023, the
laser's passes since 2018. For tidal coasts' spots (the owner, 22.11.2026: "far spots we have to make automatic too.
Also SWAN and take into account tides").

    constants(lat, lon) -> {name: (amplitude m, phase degrees)} or None (no sea cell near)
    tide(lat, lon, unix seconds...) -> metres against mean sea level (numpy), or None
    reach(lat, lon) -> the most the daily tide can stray from the mean (m): its constituents' amplitudes summed
    (None and 0 on dammed waters: NONTIDAL)
"""
import json
import math
import struct
import urllib.request

import numpy as np

FILES = "https://files.meteogust.com/tiles/tides/"
UA = {"User-Agent": "MeteoGust helper (data.meteogust.com)"}
_packs = {}
TIDAL = 0.25  # m (0.15 made the Gulf of Riga's 0.15-0.16 m tidal, 22.11.2026): a daily tide that strays this far from the mean (reach) makes a tidal coast (tables.py's levels, the survey's)
# The daily tide's constituents (the long-period SA, SSA, MF and MM carry the Baltic's seasonal swing, not a tide one
# rides through: with them Berzciems's 0.12 m read 0.18 and got a tidal coast's levels).
LONG = {"SA", "SSA", "MF", "MM"}
# Dammed waters EOT20 and the sea models count as sea: no tide (Workum on the IJsselmeer got the Wadden Sea's 1.65 m;
# its level is held behind the Afsluitdijk). [lat, lon] rings. shortcut: a list; add one when a spot on such water
# appears (the Zeeland basins behind their barriers, Lake Grevelingen).
NONTIDAL = {
    "IJsselmeer and Markermeer": [[52.93, 5.04], [53.08, 5.33], [52.98, 5.47], [52.85, 5.73], [52.60, 5.87], [52.47, 5.62],
                                  [52.30, 5.13], [52.36, 4.98], [52.60, 5.02], [52.75, 5.08]],
}


def _inside(lat, lon, ring):
    """Even-odd: a point in a [lat, lon] ring."""
    inside = False
    for (y1, x1), (y2, x2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _pack(lat, lon):
    n = 32
    x = min(max(int((lon + 180) / 360 * n), 0), 31)
    phi = math.radians(min(max(lat, -85.05), 85.05))
    y = min(max(int((1 - math.asinh(math.tan(phi)) / math.pi) / 2 * n), 0), 31)
    name = f"5-{x}-{y}"
    if name not in _packs:
        try:
            with urllib.request.urlopen(urllib.request.Request(FILES + name + ".pack", headers=UA), timeout=60) as r:
                b = r.read()
        except Exception:
            b = b""
        if len(b) < 8 or b[:4] != b"TID1":
            _packs[name] = None
        else:
            length = struct.unpack_from("<I", b, 4)[0]
            head = json.loads(b[8:8 + length])
            k = len(head["constituents"])
            rec = np.frombuffer(b[8 + length:8 + length + head["count"] * (4 + 4 * k)], "<u2").reshape(-1, 2 + 2 * k)
            _packs[name] = (head, rec)
    return _packs[name]


def constants(lat, lon):
    if any(_inside(lat, lon, ring) for ring in NONTIDAL.values()):
        return None
    p = _pack(lat, lon)
    if p is None:
        return None
    head, rec = p
    x = lon + 360 if head["lon0"] >= 0 and lon < 0 else lon
    dlat = head["lat0"] + rec[:, 0] * head["step"] - lat
    dlon = (head["lon0"] + rec[:, 1] * head["step"] - x) * math.cos(math.radians(lat))
    d = np.hypot(dlat, dlon)
    near = np.argsort(d)[:3]
    near = near[d[near] <= 0.6]
    if not len(near):
        return None
    w = 1 / np.maximum(d[near], 0.01)
    out = {}
    for c, name in enumerate(head["constituents"]):
        a = rec[near, 2 + 2 * c] / 1000.0  # (mm -> m)
        ph = np.radians(rec[near, 3 + 2 * c] / 100.0)
        re, im = (w * a * np.cos(ph)).sum() / w.sum(), (w * a * np.sin(ph)).sum() / w.sum()
        out[name] = (float(math.hypot(re, im)), float(math.degrees(math.atan2(im, re)) % 360))
    return out


def tide(lat, lon, times):
    c = constants(lat, lon)
    if c is None:
        return None
    import pyTMD  # (lazily: only the jobs that need the tide install it)

    names = list(c)
    t = np.atleast_1d(np.asarray(times, dtype=np.float64))
    pu, pf, G = pyTMD.constituents.arguments(t / 86400.0 + 40587.0, [n.lower() for n in names], corrections="FES")
    pu, pf, G = np.atleast_2d(pu), np.atleast_2d(pf), np.atleast_2d(G)
    amp = np.array([c[n][0] for n in names])
    ph = np.array([c[n][1] for n in names])
    return (pf * amp * np.cos(np.radians(G) + pu - np.radians(ph))).sum(axis=1)


def reach(lat, lon):
    """The most the daily tide strays from the mean (its constituents' amplitudes summed, the long-period ones out)."""
    c = constants(lat, lon)
    return 0.0 if c is None else float(sum(a for k, (a, _) in c.items() if k not in LONG))
