"""The met_em files' vertical and soil levels for WRF's namelist (num_metgrid_levels,
num_metgrid_soil_levels), appended to <work>/env.

    python levels.py work
"""
import sys
from pathlib import Path

from netCDF4 import Dataset

work = Path(sys.argv[1] if len(sys.argv) > 1 else "work")
first = sorted((work / "wps").glob("met_em.d01.*.nc"))[0]
with Dataset(first) as data:
    levels = len(data.dimensions["num_metgrid_levels"])
    soil = int(getattr(data, "NUM_METGRID_SOIL_LEVELS", 4))
with open(work / "env", "a") as env:
    env.write(f"NLEV={levels}\nNSOIL={soil}\n")
print(f"{first.name}: {levels} levels, {soil} soil levels")
