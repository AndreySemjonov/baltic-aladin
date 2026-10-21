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
