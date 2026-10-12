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
        first = tar.extractfile(members[1] if len(members) > 1 else members[0]).read()
except tarfile.ReadError:
    first = data
print("GRIB edition", first[7] if first[:4] == b"GRIB" else "not GRIB", "bytes", len(first))
import eccodes
handle = eccodes.codes_new_from_message(first)
count = 0
while handle is not None and count < 200:
    try:
        print(eccodes.codes_get(handle, "indicatorOfParameter"), eccodes.codes_get(handle, "indicatorOfTypeOfLevel"),
              eccodes.codes_get(handle, "level"), eccodes.codes_get(handle, "shortName"), eccodes.codes_get(handle, "Ni"),
              eccodes.codes_get(handle, "Nj"), eccodes.codes_get(handle, "latitudeOfFirstGridPointInDegrees"),
              eccodes.codes_get(handle, "longitudeOfFirstGridPointInDegrees"), eccodes.codes_get(handle, "iDirectionIncrementInDegrees"),
              eccodes.codes_get(handle, "stepRange"))
    except Exception as error:
        print("?", error)
    eccodes.codes_release(handle)
    count += 1
    offset = 0
    break
