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
    d[:, :5] = A.LAND
    lat = lambda my: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * my))))  # noqa: E731
    lon = lambda mx: mx * 360 - 180  # noqa: E731
    at = lambda r, c: [lat(box[1] + r / H * box[3]), lon(box[0] + c / W * box[2])]  # noqa: E731
    A._shallows(d, box, W, H, [{"name": "test", "depth": 0.3, "ring": [at(10, 2), at(10, 20), at(20, 20), at(20, 2)]}])
    capped = d == 0.3
    assert d[:, :5].max() == A.LAND
    assert capped[10:20, 5:20].all() and not capped[:9].any() and not capped[21:].any() and not capped[:, 21:].any()


def test_shelf():
    """A shelf caps a flat 3 m sea by the distance from the waterline near "at"; land, shallower water and far cells kept."""
    box, W, H = [0.56, 0.31, 0.00004, 0.00004], 40, 40  # (~22 m cells, the 40 well inside the shelf's 2 km)
    d = np.full((H, W), 3.0)
    d[:, :5] = A.LAND
    d[20, 30] = 0.1
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (box[1] + box[3] / 2)))))
    A._shelf(d, box, W, H, {"at": [lat, box[0] * 360 - 180], "shelf": [[0, 0.2], [300, 0.7], [700, 1.2]]}, d <= A.SEA_MIN)
    assert d[:, :5].max() == A.LAND and d[20, 30] == 0.1
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


def test_bed_lone_reading():
    """A lone reading in empty water (the survey's outer edge): no weight where the fill found nothing, so _bed leaves
    the water round it as it was (it drew rings of 0 m off Engure and Berzciems, 22.11.2026)."""
    z = np.full((101, 101), np.nan, np.float32)
    z[50, 50] = 2.5
    filled, w = A._bed_fill(z, 10.0)
    assert np.all((w > 0) == np.isfinite(filled)), "weight without a depth"
    depth = np.full((20, 20), 5.0, np.float32)
    bed = {"box": [0.5, 0.3, 0.001, 0.001], "W": 101, "H": 101, "z": filled, "w": w}
    A._bed(depth, [0.5, 0.3, 0.001, 0.001], 20, 20, bed)
    assert depth.min() >= 2.4, depth.min()  # (5 m round it, at most the reading's own 2.5 m: no 0 m ring)


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


def test_bed_under_survey():
    """_bed over a country's survey (area.surveys): the survey's 6 m channel stays 6 m under a bed reading 0.4 m (the Stör:
    the bed's fill spread its bank flats over the channel), its 0.8 m flat takes the bed's 0.4; unsurveyed 3 m takes it."""
    box, W, H = [0.5, 0.3, 0.001, 0.001], 6, 6
    depth = np.full((H, W), 3.0, np.float32)
    depth[:, :2], depth[:, 2:4] = 6.0, 0.8
    surveyed = np.zeros((H, W), bool)
    surveyed[:, :4] = True
    bed = {"box": box, "W": 30, "H": 30, "z": np.full((30, 30), 0.4, np.float32), "w": np.ones((30, 30), np.float32)}
    A._bed(depth, box, W, H, bed, None, surveyed)
    assert np.allclose(depth[:, :2], 6.0) and np.allclose(depth[:, 2:], 0.4), depth


def test_bed_river_left():
    """bed_for: a bed's river pixels (254) get no weight, though the fill reaches them from the banks' 0 m flats (the
    Lielupe "dry at -0.45", 25.11.2026); the unread water beside the flats (255) is still filled."""
    import gzip, json, tempfile
    from pathlib import Path as P

    b = np.full((60, 60), 255, np.uint8)
    b[:, :20] = round(1.4 / 0.027)  # (0 m: base -1.4, step 0.027)
    b[:, 30:40] = 254
    with tempfile.TemporaryDirectory() as d:
        (P(d) / "t.json").write_text(json.dumps({"box": [0.5, 0.3, 0.00002, 0.00002], "W": 60, "H": 60, "base": -1.4, "step": 0.027}))
        (P(d) / "t.bin.gz").write_bytes(gzip.compress(b.tobytes()))
        old, A.BEDS = A.BEDS, P(d)
        try:
            bed = A.bed_for("t")
        finally:
            A.BEDS = old
    assert (bed["w"][:, 30:40] == 0).all() and np.isnan(bed["z"][:, 30:40]).all(), bed["w"][30, 28:42]
    assert bed["w"][30, 22] > 0 and abs(bed["z"][30, 22]) < 0.05, (bed["w"][30, 22], bed["z"][30, 22])


def test_moles():
    """area.moles_from: a 20 x 300 m mole on a coast, mapped as coastline (land on the left), is thin land; the land
    behind it and the sea aren't (Tarifa's outer arm, 25.11.2026)."""
    import math

    lat0, lon0, R = 36.0, -5.6, 6371008.8
    ll = lambda x, y: [lon0 + math.degrees(x / (R * math.cos(math.radians(lat0)))), lat0 + math.degrees(y / R)]
    ring = [(-1000, -1000), (1000, -1000), (1000, 0), (10, 0), (10, 300), (-10, 300), (-10, 0), (-1000, 0), (-1000, -1000)]
    box = A.box_round(lat0, lon0, 3000.0)
    t = A.moles_from([[ll(x, y) for x, y in ring]], box, 300, 300)
    cell = lambda x, y: (150 - int(round(y / 10)), 150 + int(round(x / 10)))
    assert t[cell(0, 150)] and t[cell(0, 250)], "the mole"
    assert not t[cell(0, -500)] and not t[cell(500, -300)] and not t[cell(500, 500)] and not t[cell(0, 600)], "land, sea"
    ys, xs = np.nonzero(t)
    assert abs(xs - 150).max() <= 3 and 150 - ys.max() >= 0 and 150 - ys.min() <= 32, (xs.min(), xs.max(), ys.min(), ys.max())  # (only the mole)


def test_bank_bytes():
    """A bank 0.25 m above mean sea level and a 0.12 m lagoon round-trip through the map's bytes (DEPTH_BASE), land filled."""
    import tables as T

    d = np.array([[A.LAND, -0.25, 0.12, 5.0, 2.2]])
    b = np.frombuffer(T.depth_bytes(d), np.uint8)
    back = b * T.DEPTH_STEP + T.DEPTH_BASE
    assert abs(back[1] + 0.25) < T.DEPTH_STEP / 2 + 1e-6 and abs(back[2] - 0.12) < T.DEPTH_STEP / 2 + 1e-6 and b[3] == 254 and b[0] == b[1] and abs(back[4] - 2.2) < 0.011
    # reeds as the reed lagoon's water; land far from the sea fades to it (no rays of the nearest sea cell's depth)
    d = np.full((1, 20), A.LAND)
    d[0, 0], d[0, 1] = 2.0, A.REED
    back = np.frombuffer(T.depth_bytes(d), np.uint8) * T.DEPTH_STEP + T.DEPTH_BASE
    assert abs(back[1] - A.LAGOON) < 0.011 and abs(back[3] - A.LAGOON) < 0.011 and abs(back[19] - A.LAGOON) < 0.011


def test_icesat_span():
    """icesat.py _span: the track's stretch over the area by binary search, on tracks going north and south."""
    import icesat

    class D:  # (an h5py dataset's two needs: shape and an item)
        def __init__(self, a):
            self.a, self.shape = a, a.shape

        def __getitem__(self, i):
            return self.a[i]

    for arr in (np.linspace(58, 61, 100001), np.linspace(61, 58, 100001)):
        i0, i1 = icesat._span(D(arr), 59.53, 59.61)
        inside = (arr >= 59.53) & (arr <= 59.61)
        assert inside[i0:i1].all() and inside.sum() == i1 - i0, (i0, i1)


def test_laser_correction():
    """survey.py laser_correction: a track whose misses are -0.6 m (ours 0.6 m too deep) corrects the water by it, by
    ~0.6 within 300 m, by nothing 1.5 km away; a wild miss is capped at 1.5 m."""
    import survey as S

    rows = np.arange(100, 800)
    cols = np.full(len(rows), 400)
    miss = np.full(len(rows), -0.6)
    miss[::50] = 9.0  # (wild: capped)
    fix = S.laser_correction((900, 900), rows, cols, miss)
    assert abs(fix[450, 400] + 0.6) < 0.25 and abs(fix[450, 420] + 0.6) < 0.25, (fix[450, 400], fix[450, 420])
    assert fix[450, 550] == 0.0 and fix[450, 250] == 0.0  # (1.5 km off)


def test_laser_like_with_like():
    """survey.py laser_correction with our depths: a reef the colour reads 4.9 m and the laser 1.6 corrects the reef, not
    the 1.2 m sand 500 m off (Tarifa's harbour, 25.11.2026); a sand track's misses still correct the sand."""
    import survey as S

    Z = np.full((900, 900), 1.2)
    Z[100:110, 400:410] = 4.9
    rows, cols = np.repeat(np.arange(100, 110), 10), np.tile(np.arange(400, 410), 10)
    fix = S.laser_correction(Z.shape, rows, cols, np.full(100, -3.3), Z)
    assert abs(fix[105, 405] + 1.5) < 0.05 and fix[150, 405] == 0.0, (fix[105, 405], fix[150, 405])
    rows2 = np.arange(300, 600)
    fix = S.laser_correction(Z.shape, np.r_[rows, rows2], np.r_[cols, np.full(300, 600)], np.r_[np.full(100, -3.3), np.full(300, 1.0)], Z)
    assert abs(fix[450, 620] - 1.0) < 0.05 and abs(fix[105, 405] + 1.5) < 0.05, (fix[450, 620], fix[105, 405])


def test_flat_gaps():
    """survey.py flat_gaps: measured flats at 0.3 m with never-dry specks the colour reads 1.0 m: the specks held at the
    lowest level seen (0.47); a 10-pixel channel through the flats and the flats' edge to 1.5 m water keep their depth."""
    import survey as S

    Z = np.full((60, 60), 0.3)
    flat = np.zeros((60, 60), bool)
    flat[:, :40] = True
    flat[:, 20:30] = False  # (the channel)
    Z[:, 20:30] = 1.5
    Z[:, 40:] = 1.5  # (deeper water past the flats)
    for y, x in ((10, 5), (11, 5), (30, 12), (45, 35)):
        flat[y, x], Z[y, x] = False, 1.0
    Z2, n = S.flat_gaps(Z, flat, ~flat, 0.47)
    assert n == 4, n
    assert all(abs(Z2[y, x] - 0.47) < 1e-6 for y, x in ((10, 5), (11, 5), (30, 12), (45, 35)))
    assert (Z2[:, 20:30] == 1.5).all() and (Z2[:, 40:] == 1.5).all()
    assert (Z2[flat] == 0.3).all()


def test_muddy_dry():
    """survey.py wet_dry in muddy water (MUD_DRY): the Elbe's muddiest water (green 0.07, nir 0.075, nd -0.03) is "dry"
    by the ordinary rule and neither in muddy water; exposed mud (green 0.06, nir 0.15) dry by both; plain water wet."""
    import survey as S

    g, n = np.array([0.07, 0.06, 0.064]), np.array([0.075, 0.15, 0.025])
    wet, dry = S.wet_dry(g, n)
    assert list(dry) == [True, True, False] and list(wet) == [False, False, True]
    wet, dry = S.wet_dry(g, n, S.MUD_DRY)
    assert list(dry) == [False, True, False] and list(wet) == [False, False, True]


def test_flat_heights():
    """survey.py flat_heights: a sloping flat (-1.8 to +1.8 m over mean sea level) on a 4.4 m tide, wet in a scene when
    the tide stands above it, a tenth of the scenes misread: 95 % of the heights within 0.15 m, none over 0.35; a pixel
    always wet (never uncovered) has none."""
    import survey as S

    rng = np.random.default_rng(2)
    S_ = 80
    levels = rng.uniform(-2.2, 2.2, S_)
    days = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(S_)]
    yy, xx = np.mgrid[0:30, 0:30]
    truth = -1.8 + 3.6 * xx / 29 + 0.2 * np.sin(yy / 4)
    truth[15, 0] = -9.0  # (below every tide: always wet)
    wet = levels[:, None, None] > truth[None]
    wet ^= rng.random(wet.shape) < 0.1  # (misread)
    wet[:, 15, 0] = True
    G = np.where(wet, 0.08, 0.05).astype(np.float32)
    NI = np.where(wet, 0.02, 0.15).astype(np.float32)
    h = S.flat_heights(G, NI, days, dict(zip(days, levels)), np.ones((30, 30), bool), 2.0)
    assert np.isnan(h[15, 0]), h[15, 0]
    e = np.abs(h - truth)[1:-1, 1:-1]
    e = e[np.isfinite(e)]
    assert np.percentile(e, 95) < 0.15 and e.max() < 0.35, (np.percentile(e, 95), e.max())


def test_flat_heights_rare_dry():
    """flat_heights on the Baltic's levels: a river always wet but for 3 misread scenes in 80 (the Lielupe's brown flood
    water, 23.11.2026) has no height; a lagoon dry in just the two lowest-water scenes has one between them and the next."""
    import survey as S

    rng = np.random.default_rng(5)
    levels = rng.uniform(-0.5, 0.6, 80)
    days = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(80)]
    wet = np.ones((80, 12, 12), bool)
    for y in range(1, 12):
        for x in range(12):
            wet[rng.choice(80, 3, replace=False), y, x] = False
    low = np.argsort(levels)
    wet[low[:2], 0, :] = False
    G = np.where(wet, 0.08, 0.05).astype(np.float32)
    NI = np.where(wet, 0.02, 0.15).astype(np.float32)
    # (Rows 6-11 instead 6 scenes too dark to read, under a low sun: 0.0001 both, NDWI 0.)
    G[:, 6:], NI[:, 6:] = 0.08, 0.02
    for y in range(6, 12):
        for x in range(12):
            k = rng.choice(80, 6, replace=False)
            G[k, y, x] = NI[k, y, x] = 1e-4
    h = S.flat_heights(G, NI, days, dict(zip(days, levels)), np.ones((12, 12), bool), 0.6)
    assert np.isnan(h[1:]).all(), np.isfinite(h[1:]).sum()
    s = np.sort(levels)
    assert np.isfinite(h[0]).all() and (h[0] > s[1]).all() and (h[0] < s[2]).all(), h[0]


def test_seasonal():
    """survey.py seasonal: behind a beach (sea in columns 0-9, 10 m pixels) a lagoon wet Oct-Apr and dry Jun-Sep (Los
    Lances, 23.11.2026) gets its months; the swash by the sea none; with a swing asked a strip wet a third of the time all
    year none, without one its third every month."""
    import survey as S

    rng = np.random.default_rng(3)
    days = [f"{2023 + i // 60}-{1 + (i // 5) % 12:02d}-{1 + i % 5:02d}" for i in range(120)]
    mo = np.array([int(d[5:7]) for d in days])
    wet = np.zeros((120, 30, 40), bool)
    wet[:, :, :10] = True
    wet[:, :, 10:12] = rng.random((120, 30, 2)) < 0.5  # (the swash)
    winter = np.isin(mo, [10, 11, 12, 1, 2, 3, 4])[:, None, None]
    wet[:, 5:20, 20:30] = winter & (rng.random((120, 15, 10)) < 0.9)
    wet[:, 22:28, 20:35] = rng.random((120, 6, 15)) < 0.33
    G, NI = np.where(wet, 0.08, 0.05).astype(np.float32), np.where(wet, 0.02, 0.15).astype(np.float32)
    sea = np.zeros((30, 40), bool)
    sea[:, :10] = True
    m = S.seasonal(G, NI, days, sea, np.zeros_like(sea), swing=0.4)
    assert m is not None
    got = np.isfinite(m[0])
    assert got[5:20, 20:30].all() and got.sum() == 150, got.sum()
    assert (m[0][5:20, 20:30] > 0.6).all() and (m[6][5:20, 20:30] < 0.1).all(), (m[0][5:20, 20:30].min(), m[6][5:20, 20:30].max())
    m = S.seasonal(G, NI, days, sea, np.zeros_like(sea))
    got = np.isfinite(m[0])
    assert got[22:28, 20:35].all() and got.sum() == 240 and not got[:, 10:12].any(), got.sum()
    # (Recent scenes weigh more since 23.11.2026, so a month stands on fewer of them: noisier.)
    assert np.nanmax(np.abs(m[:12, 22:28, 20:35] - 0.33)) < 0.4, np.nanmax(np.abs(m[:12, 22:28, 20:35] - 0.33))
    # (Plane 12, plants in every scene: the dry land here reads as plants, near-infrared 0.15 over green 0.05; never the
    # lagoon, the strip or the swash.)
    pl = m[12] == 1.0
    assert pl[0:5, 20:40].all() and not pl[5:20, 20:30].any() and not pl[22:28, 20:35].any() and not pl[:, :12].any()
    # Plane 13: a lagoon the tide floods above +0.3 m in its open months (Oct-Apr), shut in summer (Los Lances, the owner,
    # 23.11.2026): its height from the open months' scenes (byte = height x 50 + 127), none where it has no split.
    lev = rng.uniform(-0.6, 0.6, 120)
    wet[:, 5:20, 20:30] = winter & (lev[:, None, None] > 0.3)
    G, NI = np.where(wet, 0.08, 0.05).astype(np.float32), np.where(wet, 0.02, 0.15).astype(np.float32)
    m = S.seasonal(G, NI, days, sea, np.zeros_like(sea), lv=dict(zip(days, lev)), tide_reach=0.6)
    h = (np.round(m[13][5:20, 20:30] * 254) - 127) / 50
    assert np.isfinite(h).all() and np.abs(h - 0.3).max() < 0.1, (np.nanmin(h), np.nanmax(h))
    # (The strip wet a third of the time by chance: a few pixels pass the waterline's fifth on the wrong side, 2 of 90.)
    assert np.isfinite(m[13][22:28, 20:35]).mean() <= 0.05, np.isfinite(m[13][22:28, 20:35]).sum()
    # A lagoon the tide doesn't drive (wet by the month and the river, Los Lances's check, 23.11.2026): its heights
    # explain no more than the month does, so none; its months kept.
    wet[:, 5:20, 20:30] = winter & (rng.random((120, 15, 10)) < 0.8)
    G, NI = np.where(wet, 0.08, 0.05).astype(np.float32), np.where(wet, 0.02, 0.15).astype(np.float32)
    m = S.seasonal(G, NI, days, sea, np.zeros_like(sea), lv=dict(zip(days, lev)), tide_reach=0.6)
    assert not np.isfinite(m[13]).any() and np.isfinite(m[0][5:20, 20:30]).all(), np.isfinite(m[13]).sum()


def test_laser_fit():
    """survey.py laser_fit: the colour-to-depth line from laser pixels, a few wild ones (a boulder, a stray photon) left
    out, the crest on the line."""
    import survey as S

    rng = np.random.default_rng(1)
    z = rng.uniform(0.3, 2.0, 400)  # (the laser's depths: the accurate one)
    L = (z - 0.5) / -0.6 + rng.normal(0, 0.4, 400)  # (the colour, noisy; the truth a = 0.5, b = -0.6)
    L[:20] -= 3.0  # (wild)
    a, b, err = S.laser_fit(L, z, (0.3 - 0.5) / -0.6, 0.3)
    assert abs(a - 0.5) < 0.15 and abs(b + 0.6) < 0.08, (a, b, err)  # (fitted from the colour, b came out ~-0.35)
    assert S.laser_fit(-L, z, 0.0, 0.3) is None  # (light falling off the wrong way)


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
    test_bed_under_survey()
    test_bed_lone_reading()
    test_bank_bytes()
    test_survey_ring()
    test_icesat_span()
    test_laser_fit()
    test_muddy_dry()
    test_laser_correction()
    test_laser_like_with_like()
    test_bed_river_left()
    test_moles()
    test_flat_heights()
    test_flat_heights_rare_dry()
    test_seasonal()
    test_flat_gaps()
    print("ok")
