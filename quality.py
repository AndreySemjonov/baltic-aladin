"""Checks of a model's values before they are published (docs/RAIN_MAP_FIX.md in the app's repository,
R3; 12.10.2026: DINI's rain read as a rate drew a block of 300,000 mm/h, AROME's missing corners
storms of 9999 m/s, and both reached the phones). Each check looks at one hour's fields on the
model's own grid (no data left out) and says what is wrong, or None:

- the wind (east, north, gust): over 60 m/s, or a gust over 85 m/s, on more than 0.1 % of the points;
- the rain (mm in the hour): over 60 mm/h on more than 0.1 %, or an average over 4 mm/h;
- the air (C): below -60 or above 55 on more than 0.1 %;
- any field: one value over more than 90 % of the points where it should vary (a constant block).

A run's results go to tiles/<set>/check.json (read by the admin panel): when it was checked, the
hours checked, and what failed (by model and hour).
"""
import json
from datetime import datetime, timezone

import numpy as np

SHARE = 0.001


def _finite(values):
    values = np.asarray(values, dtype=np.float32)
    return values[np.isfinite(values)]


def _over(values, limit):
    v = _finite(values)
    return v.size and float(np.mean(v > limit)) > SHARE


def _constant(values):
    v = _finite(values)
    if v.size < 1000:
        return False
    common = np.bincount(np.clip(np.rint(v * 10).astype(np.int64) - int(np.rint(v.min() * 10)), 0, None)).max()
    return common / v.size > 0.9


def wind(east, north, gust=None):
    speed = np.hypot(np.asarray(east, dtype=np.float32), np.asarray(north, dtype=np.float32))
    if _over(speed, 60):
        return f"wind over 60 m/s on {float(np.mean(_finite(speed) > 60)) * 100:.1f} %"
    if gust is not None and _over(gust, 85):
        return f"gust over 85 m/s on {float(np.mean(_finite(gust) > 85)) * 100:.1f} %"
    if _constant(speed):
        return "wind the same nearly everywhere"
    return None


def weather(air=None, rain=None):
    if rain is not None:
        r = _finite(rain)
        if _over(r, 60):
            return f"rain over 60 mm/h on {float(np.mean(r > 60)) * 100:.1f} %"
        if r.size and float(np.mean(r)) > 4:
            return f"rain averaging {float(np.mean(r)):.1f} mm/h"
    if air is not None:
        a = _finite(air)
        if a.size and float(np.mean((a < -60) | (a > 55))) > SHARE:
            return f"air outside -60..55 C on {float(np.mean((a < -60) | (a > 55))) * 100:.1f} %"
        if _constant(a):
            return "air the same nearly everywhere"
    return None


class Report:
    """One run's checks: `fail(model, hour, reason)` for each failed hour, `count()` per hour checked."""

    def __init__(self, name):
        self.name, self.checked, self.failures = name, 0, []

    def count(self):
        self.checked += 1

    def fail(self, model, unix_time, reason):
        self.failures.append({"model": model, "time": int(unix_time), "reason": reason})
        print(f"CHECK {self.name}: {model} +{datetime.fromtimestamp(unix_time, timezone.utc):%d %H:%M}: {reason}", flush=True)

    def failed_models(self):
        return sorted({f["model"] for f in self.failures})

    def body(self, run, published):
        return json.dumps({"set": self.name, "run": run, "checked": self.checked, "published": published,
                           "time": int(datetime.now(timezone.utc).timestamp()), "failures": self.failures[:50]},
                          separators=(",", ":")).encode()
