# The helper's jobs off GitHub (the owner, 21.10.2026: the Tower now, the Mac mini from the end of
# the week): one image with every package the workflows install each time, the code mounted from the
# machine's copy of this repo (no rebuild when it changes). The same image builds on the Mac's ARM chip.
#
#   docker build -t meteogust-helper runner/
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The union of the workflows' pip installs.
RUN pip install --no-cache-dir eccodes numpy pillow scipy omfiles pyproj h5py netCDF4 copernicusmarine s3fs pyshp

COPY measure.py /measure.py
WORKDIR /code
