"""The Copernicus Marine Baltic Sea model (BALTICSEA_ANALYSISFORECAST_PHY_003_006, dataset
cmems_mod_bal_phy_anfc_PT1H-i: hourly, about 1.7 km, the whole Baltic, about 5 days ahead):
sea level, surface temperature and current over the water map's box, for the coasts NEMO-EST
doesn't reach (the Latvian west coast, Lithuania, the open sea).

Free data, but it needs a Copernicus Marine account: the helper reads its login from the
environment (COPERNICUSMARINE_SERVICE_USERNAME, COPERNICUSMARINE_SERVICE_PASSWORD, GitHub
secrets); without one the water map is NEMO-EST only. Source: E.U. Copernicus Marine Service
Information (https://marine.copernicus.eu/).
"""
import os

import numpy as np

DATASET = "cmems_mod_bal_phy_anfc_PT1H-i"


def available():
    return bool(os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME") and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD"))


def load(south, north, west, east, start, end):
    """Hourly fields between `start` and `end` (UTC datetimes): {"times": [unix], "lats", "lons",
    "level" (m), "temperature" (°C), "u", "v" (m/s)}, NaN over land; None when unavailable."""
    if not available():
        print("Copernicus: no login, the water map stays NEMO-EST only")
        return None
    try:
        import copernicusmarine
        ds = copernicusmarine.open_dataset(
            dataset_id=DATASET, variables=["sla", "thetao", "uo", "vo"],
            minimum_longitude=west, maximum_longitude=east, minimum_latitude=south, maximum_latitude=north,
            start_datetime=start.strftime("%Y-%m-%dT%H:%M:%S"), end_datetime=end.strftime("%Y-%m-%dT%H:%M:%S"),
            minimum_depth=0, maximum_depth=2)
        surface = {}
        for name in ("thetao", "uo", "vo"):
            values = ds[name]
            if "depth" in values.dims:
                values = values.isel(depth=0)
            surface[name] = values
        times = [int(t) for t in ds["time"].values.astype("datetime64[s]").astype(np.int64)]
        out = {"times": times, "lats": ds["latitude"].values.astype(np.float64), "lons": ds["longitude"].values.astype(np.float64),
               "level": ds["sla"].values.astype(np.float32), "temperature": surface["thetao"].values.astype(np.float32),
               "u": surface["uo"].values.astype(np.float32), "v": surface["vo"].values.astype(np.float32)}
        print(f"Copernicus: {len(times)} hours, {len(out['lats'])}x{len(out['lons'])} cells")
        return out
    except Exception as error:  # the water map then stays NEMO-EST only
        print(f"Copernicus unavailable: {error}")
        return None
