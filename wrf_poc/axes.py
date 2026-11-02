"""The WRF grid's axes against east and north, from the grid's own latitudes and longitudes (XLAT, XLONG).

WRF writes U10/V10 along its grid's axes; on a Lambert grid those are tilted from east and north by up to tens of
degrees. COSALPHA and SINALPHA give the tilt, but south of the equator their sign turned our winds the wrong way:
Cape Town's WRF came out 33 degrees clockwise of the airports' readings (2 x its 16.7 degree tilt; the owner,
02.11.2026: "wind direction wrong ... not turns into such onshore"), while Tarifa's matched. The tilt measured from
the grid itself is right in either hemisphere and for any projection.
"""
import numpy as np


def grid_north(lat, lon):
    """Per cell, the cosine and sine of the bearing (from true north, clockwise) of the grid's +y axis (south_north).
    lat, lon: 2D arrays [south_north, west_east] in degrees."""
    d_lat = np.gradient(np.asarray(lat, dtype=np.float64), axis=0)
    d_lon = np.gradient(np.asarray(lon, dtype=np.float64), axis=0)
    d_lon = (d_lon + 180) % 360 - 180  # across the date line
    d_east = d_lon * np.cos(np.radians(lat))
    bearing = np.arctan2(d_east, d_lat)
    return np.cos(bearing), np.sin(bearing)


def to_earth(u, v, cos_b, sin_b):
    """Grid-relative u (along +x) and v (along +y) to east and north: +y is `bearing` clockwise of north, +x 90 more."""
    return u * cos_b + v * sin_b, v * cos_b - u * sin_b
