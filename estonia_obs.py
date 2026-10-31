"""Estonia's weather stations' observations kept hour by hour, for the radar's reality check (WindguruStationWatch
backend/lab/radar_fringe_check.py: present weather such as "Light rain" against what the radar drew; the owner,
31.10.2026: "Estonian feed is open, write it. make next"). The feed shows only the newest hour's observations (its
timestamp is just when it answered), so each new one is saved as <out>/YYYYMMDDHHMM.xml.gz, the UTC time it was first
seen (the observations: that hour's start), when it differs from the last saved; run every 10 minutes.

    python estonia_obs.py [--out DIR]     # default ~/meteogust-fast/keep/ee-obs
"""
import gzip
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://www.ilmateenistus.ee/ilma_andmed/xml/observations.php"
AGENT = {"User-Agent": "baltic-aladin (https://github.com/AndreySemjonov/baltic-aladin)"}

out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path.home() / "meteogust-fast" / "keep" / "ee-obs"
with urllib.request.urlopen(urllib.request.Request(URL, headers=AGENT), timeout=60) as reply:
    data = reply.read()
stamp = re.search(rb'<observations timestamp="(\d+)"', data)
if not stamp or b"<station>" not in data:
    sys.exit("No observations in the feed's answer")
name = out / f"{datetime.fromtimestamp(int(stamp.group(1)), timezone.utc):%Y%m%d%H%M}.xml.gz"
saved = sorted(out.glob("*.xml.gz"))
body = lambda d: d[re.search(rb'timestamp="\d+"', d).end():]
if saved and body(gzip.decompress(saved[-1].read_bytes())) == body(data):
    print("Nothing new since", saved[-1].name)
else:
    out.mkdir(parents=True, exist_ok=True)
    name.write_bytes(gzip.compress(data))
    print("Saved", name.name, len(data), "bytes")
