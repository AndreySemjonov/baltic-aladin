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


def test_bank_bytes():
    """A bank 0.25 m above mean sea level and a 0.12 m lagoon round-trip through the map's bytes (DEPTH_BASE), land filled."""
    import tables as T

    d = np.array([[-1.0, -0.25, 0.12, 5.0, 2.2]])
    b = np.frombuffer(T.depth_bytes(d), np.uint8)
    back = b * T.DEPTH_STEP + T.DEPTH_BASE
    assert abs(back[1] + 0.25) < T.DEPTH_STEP / 2 + 1e-6 and abs(back[2] - 0.12) < T.DEPTH_STEP / 2 + 1e-6 and b[3] == 254 and b[0] == b[1] and abs(back[4] - 2.2) < 0.011


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
    test_bank_bytes()
    test_survey_ring()
    print("ok")
