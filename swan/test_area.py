"""Checks for area.py's hand-given features (python3 swan/test_area.py; no downloads)."""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import area as A  # noqa: E402


def test_shallows():
    """An outline over a flat 3 m sea caps exactly the sea cells inside it, land untouched."""
    box, W, H = [0.56, 0.31, 0.0004, 0.0004], 40, 40
    d = np.full((H, W), 3.0)
    d[:, :5] = -1
    lat = lambda my: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * my))))  # noqa: E731
    lon = lambda mx: mx * 360 - 180  # noqa: E731
    at = lambda r, c: [lat(box[1] + r / H * box[3]), lon(box[0] + c / W * box[2])]  # noqa: E731
    A._shallows(d, box, W, H, [{"name": "test", "depth": 0.3, "ring": [at(10, 2), at(10, 20), at(20, 20), at(20, 2)]}])
    capped = d == 0.3
    assert d[:, :5].max() == -1
    assert capped[10:20, 5:20].all() and not capped[:9].any() and not capped[21:].any() and not capped[:, 21:].any()


def test_shelf():
    """A shelf caps a flat 3 m sea by the distance from the waterline near "at"; land, shallower water and far cells kept."""
    box, W, H = [0.56, 0.31, 0.00004, 0.00004], 40, 40  # (~22 m cells, the 40 well inside the shelf's 2 km)
    d = np.full((H, W), 3.0)
    d[:, :5] = -1
    d[20, 30] = 0.1
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2)))))
    A._shelf(d, box, W, H, {"at": [lat, box[0] * 360 - 180], "shelf": [[0, 0.2], [300, 0.7], [700, 1.2]]}, d < 0)
    assert d[:, :5].max() == -1 and d[20, 30] == 0.1
    assert 0.2 <= d[20, 5] < 0.3 and d[20, 6] < d[20, 15] < d[20, 25] <= 1.2


def test_bed_fill():
    """A hole between a bar (0.2 m) and deeper water (1.4 m) filled from both sides, smoothly; nothing far from a
    reading; the weight fading to the window's edge."""
    z = np.full((200, 200), np.nan, np.float32)
    z[:, 60:80], z[:, 100:120] = 0.2, 1.4  # bar, then a 20-pixel (200 m) hole, then deeper water; land past 120
    f, w = A._bed_fill(z, 10.0)
    mid = f[100, 80:100]
    assert np.all(np.isfinite(mid)) and 0.2 < mid.mean() < 1.4 and np.all(np.diff(mid) >= -1e-3), mid
    assert 0.8 < w[100, 90] < 0.9 and w[100, 70] == 1.0 and w[100, 150] < 0.1 and np.isnan(f[100, 199])
    assert w[2, 70] < 0.1 and w[30, 70] == 1.0  # (the window's edge 250 m: 25 pixels)


def test_bed_cover():
    """_bed's cover: the satellite bed's share of each cell, 1 where it read, 0 beyond it (the map's "measured")."""
    box, W, H = [0.5, 0.3, 0.001, 0.001], 8, 8
    depth = np.full((H, W), 3.0, np.float32)
    # (A bed over the grid's left half, 1.0 m everywhere it read.)
    bed = {"box": [0.5, 0.3, 0.0005, 0.001], "W": 20, "H": 40, "z": np.full((40, 20), 1.0, np.float32),
           "w": np.ones((40, 20), np.float32)}
    cover = np.zeros((H, W), np.float32)
    A._bed(depth, box, W, H, bed, cover)
    assert np.allclose(cover[:, :4], 1) and np.allclose(cover[:, 4:], 0), cover
    assert np.allclose(depth[:, :4], 1.0) and np.allclose(depth[:, 4:], 3.0), depth


def test_bank_bytes():
    """A bank 0.25 m above mean sea level and a 0.12 m lagoon round-trip through the map's bytes (DEPTH_BASE), land filled."""
    import tables as T

    d = np.array([[-1.0, -0.25, 0.12, 5.0, 2.2]])
    b = np.frombuffer(T.depth_bytes(d), np.uint8)
    back = b * T.DEPTH_STEP + T.DEPTH_BASE
    assert abs(back[1] + 0.25) < T.DEPTH_STEP / 2 + 1e-6 and abs(back[2] - 0.12) < T.DEPTH_STEP / 2 + 1e-6 and b[3] == 254 and b[0] == b[1] and abs(back[4] - 2.2) < 0.011
    # reeds as the reed lagoon's water; land far from the sea fades to it (no rays of the nearest sea cell's depth)
    d = np.full((1, 20), -1.0)
    d[0, 0], d[0, 1] = 2.0, -2.0
    back = np.frombuffer(T.depth_bytes(d), np.uint8) * T.DEPTH_STEP + T.DEPTH_BASE
    assert abs(back[1] - A.LAGOON) < 0.011 and abs(back[3] - A.LAGOON) < 0.011 and abs(back[19] - A.LAGOON) < 0.011


def test_survey_ring():
    """survey.py's outline of a pixel block: its four corners (metres from the window's corner, rows south), closed."""
    import survey as S

    comp = np.zeros((30, 30), bool)
    comp[5:15, 3:23] = True
    ring = S._ring(comp, {"x0": 0.0, "y0": 0.0}, lambda x, y: (list(x), list(y)))
    assert ring[0] == ring[-1] and len(ring) == 5, ring
    assert sorted({(lat, lon) for lat, lon in ring}) == [(-150, 30), (-150, 230), (-50, 30), (-50, 230)], ring


if __name__ == "__main__":
    test_shallows()
    test_shelf()
    test_bed_fill()
    test_bed_cover()
    test_bank_bytes()
    test_survey_ring()
    print("ok")
