"""What KNMI's HARMONIE-AROME Netherlands files look like (names, sizes, one file's contents), to
build the regional tiles' HARM-NL on: the key from KNMI_API_KEY, never printed."""
import io
import json
import os
import tarfile
import urllib.request

API = "https://api.dataplatform.knmi.nl/open-data/v1/datasets/harmonie_arome_cy43_p1/versions/1.0/files"
key = os.environ.get("KNMI_API_KEY", "")
if not key:
    raise SystemExit("KNMI_API_KEY is not set (GitHub: Settings, Secrets and variables, Actions)")


def get(url, raw=False, limit=None):
    request = urllib.request.Request(url, headers={"Authorization": key})
    if limit:
        request.add_header("Range", f"bytes=0-{limit - 1}")
    with urllib.request.urlopen(request, timeout=300) as reply:
        return reply.read() if raw else json.load(reply)


listing = get(API + "?maxKeys=10&orderBy=created&sorting=desc")
for f in listing.get("files", []):
    print(f["filename"], f.get("size"), f.get("created"))
name = listing["files"][0]["filename"]
url = get(f"{API}/{name}/url")["temporaryDownloadUrl"]
data = urllib.request.urlopen(url, timeout=600).read()
print("downloaded", name, len(data))
try:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = tar.getmembers()
        print(len(members), "members; first", [m.name for m in members[:5]], "last", members[-1].name, members[0].size)
        first = tar.extractfile(next(m for m in members if m.name.endswith('_00600_GB'))).read()
except tarfile.ReadError:
    first = data
print("GRIB edition", first[7] if first[:4] == b"GRIB" else "not GRIB", "bytes", len(first))
import eccodes
import tempfile
with tempfile.NamedTemporaryFile(suffix=".grb", delete=False) as out:
    out.write(first)
    path = out.name
with open(path, "rb") as f:
    count = 0
    while True:
        handle = eccodes.codes_grib_new_from_file(f)
        if handle is None:
            break
        get = lambda k: eccodes.codes_get(handle, k) if eccodes.codes_is_defined(handle, k) else None
        print(count, "param", get("indicatorOfParameter"), "table", get("table2Version"), "levtype", get("indicatorOfTypeOfLevel"),
              "level", get("level"), "short", get("shortName"), "step", get("stepRange"), "grid", get("Ni"), get("Nj"),
              get("latitudeOfFirstGridPointInDegrees"), get("longitudeOfFirstGridPointInDegrees"),
              get("latitudeOfLastGridPointInDegrees"), get("longitudeOfLastGridPointInDegrees"), get("jScansPositively"))
        eccodes.codes_release(handle)
        count += 1
print(count, "messages")
