"""The fit for lab_lowsun_clouds.py's table: where the mask says clear at an airport, cloud when the Cloud Type RGB's green
and blue are both at least a + b x the sun's height (degrees). Each day scored with the rule fitted on the other days
(the most overcast caught with at most 2 % of those days' clear reports called cloud), by the sun's height.

    python lab_lowsun_fit.py [lab-lowsun.csv]

Result 06.11.2026 (15.10-05.11, 20 airports, 391 daylight frames, 5,853 reports): min(green, blue) >= 16 + 9 x sun
(each day left out: 14-20, 8-9). Of the overcast reports the mask called clear 288 of 527 caught, of the clear reports
it left clear 26 of 1,362 called cloud; by the sun's height: under 5 deg 84/185 and 11/261, 5-10 112/154 and 10/250,
10-15 87/132 and 4/340, over 15 5/56 and 1/511.
"""
import csv
import sys
from collections import defaultdict

ALLOWED = 0.02
BINS = ((-90, 5), (5, 10), (10, 15), (15, 90))


def bright(r):
    return min(r["green"], r["blue"])


def fit(rows):
    """(a, b): the most overcast caught with clear called cloud at most ALLOWED of the clear ones."""
    clear = [r for r in rows if r["sky"] == "clear"]
    best = None
    for b in range(0, 16):
        for a in range(0, 256, 2):
            false = sum(bright(r) >= a + b * r["sun"] for r in clear)
            if false > ALLOWED * max(len(clear), 1):
                continue
            caught = sum(bright(r) >= a + b * r["sun"] for r in rows if r["sky"] == "cloudy")
            if best is None or caught > best[0]:
                best = (caught, a, b)
            break  # the lowest a allowed for this b catches the most
    return best and best[1:]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "lab-lowsun.csv"
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        r.update(sun=float(r["sun"]), mask=float(r["mask"]), red=int(r["red"]), green=int(r["green"]), blue=int(r["blue"]))
        r["day"] = r["time"][:10]
        rows.append(r)
    # Where the mask already says cloud nothing changes: only its clear calls are candidates.
    missed = [r for r in rows if r["mask"] < 0.34]
    print(f"{len(rows)} reports; the mask clear at {len(missed)}")
    print("\nThe mask alone, by the sun's height: overcast it missed / overcast, clear it called cloud / clear")
    for lo, hi in BINS:
        part = [r for r in rows if lo <= r["sun"] < hi]
        oc = [r for r in part if r["sky"] == "cloudy"]
        cl = [r for r in part if r["sky"] == "clear"]
        print(f"  sun {lo:2}-{hi:2}: {sum(r['mask'] < 0.34 for r in oc):4} / {len(oc):4}   {sum(r['mask'] >= 0.34 for r in cl):4} / {len(cl):4}")
    days = sorted({r["day"] for r in rows})
    gained = defaultdict(lambda: [0, 0, 0, 0])  # bin: overcast caught, overcast missed by the mask, clear called cloud, clear the mask left clear
    rules = []
    for day in days:
        rule = fit([r for r in missed if r["day"] != day])
        rules.append((day, rule))
        for r in missed:
            if r["day"] != day:
                continue
            b = next(i for i, (lo, hi) in enumerate(BINS) if lo <= r["sun"] < hi)
            hit = rule is not None and bright(r) >= rule[0] + rule[1] * r["sun"]
            g = gained[b]
            if r["sky"] == "cloudy":
                g[0] += hit
                g[1] += 1
            else:
                g[2] += hit
                g[3] += 1
    print("\nThe Cloud Type rule, each day fitted on the others: of the overcast the mask missed, caught; of the clear it left clear, called cloud")
    for i, (lo, hi) in enumerate(BINS):
        g = gained[i]
        print(f"  sun {lo:2}-{hi:2}: caught {g[0]:4} / {g[1]:4}   called cloud {g[2]:4} / {g[3]:4}")
    print("\nThe rule on all days:", fit(missed), " per day left out:", sorted({rule for _, rule in rules if rule}))


if __name__ == "__main__":
    main()
