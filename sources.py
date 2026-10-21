"""Where the helper reads its open data from, by where it runs (the owner, 21.10.2026: from home in Latvia the
US copies are slow: Open-Meteo's bucket in Oregon ~8 MB/s a stream and ~17 MB/s however many; ECMWF's own
server ~7 MB/s a stream; their mirrors in Frankfurt and Google's 29-36 MB/s a stream).

MG_REGION=eu (the home machine: the Tower, the Mac mini) puts the European copies first; anything else (GitHub's
runners, next to the US copies) keeps the US ones first. Every list is tried in order (a mirror missing a file
or late: the next).
"""
import os

REGION = os.environ.get("MG_REGION", "us").lower()
EU = REGION == "eu"

# ECMWF's open data: its own server and its mirrors, the same paths under each (<day>/<hh>z/ifs/...).
ECMWF_OWN = "https://data.ecmwf.int/forecasts"
ECMWF_AWS = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
ECMWF_GOOGLE = "https://storage.googleapis.com/ecmwf-open-data"
ECMWF = [ECMWF_AWS, ECMWF_GOOGLE, ECMWF_OWN] if EU else [ECMWF_OWN, ECMWF_GOOGLE, ECMWF_AWS]

# Open-Meteo's open data: their bucket in Oregon, or at home our copy on R2 (tools/mirror in the app's repo:
# ECMWF 9 km, ICON's world and GFS 0.13°, copied as each run completes; the Tower got 73 MB/s from R2 against
# ~17 from Oregon, 21.10.2026). A run is read from the copy only once its _complete.json is there.
OPEN_METEO = "https://openmeteo.s3.amazonaws.com/"
MIRROR = "https://files.meteogust.com/mirror/"
_copied = {}


def om_url(key):
    """A data_spatial/... file's address: our copy when at home and its run is copied whole, else Oregon."""
    import time
    import urllib.request
    if EU and key.startswith("data_spatial/"):
        folder = key.rsplit("/", 1)[0] + "/"
        known = _copied.get(folder)
        if known is None or (known is not True and time.time() - known > 120):
            try:
                request = urllib.request.Request(MIRROR + folder + "_complete.json", method="HEAD",
                                                 headers={"User-Agent": "MeteoGust helper (data.meteogust.com)"})
                with urllib.request.urlopen(request, timeout=20) as reply:
                    _copied[folder] = reply.status == 200 or time.time()
            except Exception:
                _copied[folder] = time.time()
        if _copied[folder] is True:
            return MIRROR + key
    return OPEN_METEO + key
