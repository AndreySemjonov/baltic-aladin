"""WRF's gusts from its boundary layer (the owner, 20.10.2026, "G1"): WSPD10MAX is only the hour's
strongest 10 m mean wind, 0.3-0.5 m/s over the mean at 1 km (Tarifa 16 UTC: 6.7 gusting 7.6, where
real gusts run about 1.3 x the mean). Gusts are faster air from higher up mixed down by the day's
stirring, so as NOAA's WRF post-processing does it (NCEP UPP, CALGUST): the wind at the boundary
layer's top (PBLH), and of its excess over the 10 m wind the share 1 - min(0.5, PBLH / 2000 m) added
to the 10 m wind. Here the wind at the top is read between the two model levels round it (UPP takes
the level above), so the gusts don't jump in steps across the map. Never below WSPD10MAX or the wind.

    python wrf_poc/gust.py WRFOUT [LAT LON]      the hours' mean wind, old and new gusts (a check)
"""
import sys

import numpy as np

G = 9.81


def hour(data, t):
    """One hour's gust (m/s) on the grid, from an open wrfout (netCDF4 Dataset)."""
    u10, v10 = np.asarray(data["U10"][t]), np.asarray(data["V10"][t])
    wind = np.hypot(u10, v10)
    pbl = np.asarray(data["PBLH"][t])
    # The mass levels' heights above the ground (from the staggered geopotential) and their wind
    # (U and V staggered: the two faces' mean; the grid's tilt doesn't change a speed).
    z = (np.asarray(data["PH"][t]) + np.asarray(data["PHB"][t])) / G
    above = 0.5 * (z[:-1] + z[1:]) - np.asarray(data["HGT"][t])
    u, v = np.asarray(data["U"][t]), np.asarray(data["V"][t])
    speed = np.hypot(0.5 * (u[..., :-1] + u[..., 1:]), 0.5 * (v[:, :-1, :] + v[:, 1:, :]))
    # The wind at the boundary layer's top: between the level below and the one above it.
    levels = above.shape[0]
    upper = np.clip((above <= pbl).sum(axis=0), 1, levels - 1)
    lower = upper - 1
    z0, z1 = np.take_along_axis(above, lower[None], 0)[0], np.take_along_axis(above, upper[None], 0)[0]
    s0, s1 = np.take_along_axis(speed, lower[None], 0)[0], np.take_along_axis(speed, upper[None], 0)[0]
    share = np.clip((pbl - z0) / np.maximum(z1 - z0, 1), 0, 1)
    top = s0 + (s1 - s0) * share
    gust = wind + np.maximum(top - wind, 0) * (1 - np.minimum(0.5, pbl / 2000))
    if "WSPD10MAX" in data.variables:
        gust = np.maximum(gust, np.asarray(data["WSPD10MAX"][t]))
    return np.maximum(gust, wind)


def all_hours(data):
    """Every hour's gust in a wrfout: (hours, rows, columns)."""
    return np.stack([hour(data, t) for t in range(len(data["Times"]))])


def main():
    from netCDF4 import Dataset
    with Dataset(sys.argv[1]) as data:
        cell = None
        if len(sys.argv) > 3:
            lat, lon = float(sys.argv[2]), float(sys.argv[3])
            xlat, xlon = np.asarray(data["XLAT"][0]), np.asarray(data["XLONG"][0])
            cell = np.unravel_index(np.argmin((xlat - lat) ** 2 + (xlon - lon) ** 2), xlat.shape)
        for t in range(len(data["Times"])):
            stamp = "".join(c.decode() for c in data["Times"][t])
            wind = np.hypot(np.asarray(data["U10"][t]), np.asarray(data["V10"][t]))
            old = np.maximum(np.asarray(data["WSPD10MAX"][t]), wind)
            new = hour(data, t)
            pbl = np.asarray(data["PBLH"][t])
            line = (f"{stamp}  wind {wind.mean():5.2f}  old gust {old.mean():5.2f}  new {new.mean():5.2f}"
                    f"  ratio {new.mean() / wind.mean():.2f}  PBLH {pbl.mean():5.0f} m")
            if cell:
                line += f"  | point {wind[cell]:4.1f} old {old[cell]:4.1f} new {new[cell]:4.1f} PBLH {pbl[cell]:4.0f}"
            print(line)


if __name__ == "__main__":
    main()
