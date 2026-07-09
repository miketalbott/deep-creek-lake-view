"""Independent verification of the cumulative viewshed math.

For a handful of test cells (shoreline vs uphill near the lake's widest
basin) this re-computes visible lake area two independent ways:

1. Forward GDAL viewshed: observer ON the land cell at eye height, checking
   which lake sample points it sees (direct formulation of the question --
   the production pipeline uses the inverted/reciprocal formulation).

2. Pure-numpy brute-force raytrace: march along the sightline from the eye
   to every lake sample point, bilinearly interpolating the flattened DEM
   and applying the same curvature/refraction adjustment. No GDAL involved.

It also reports the azimuth histogram of visible water to confirm the full
360-degree field of view is used.
"""

import json
import sys

import numpy as np
import rasterio
from osgeo import gdal

from common import (
    CURV_COEFF,
    DEM_FLAT,
    LAKE_POINTS_NPZ,
    LAKE_SAMPLE_AREA,
    MASK_TIF,
    META_JSON,
    OBS_HEIGHT_WATER,
    layer_tif,
)

gdal.UseExceptions()
R_EARTH = 6378137.0
EYE = 2.0
ACRE = 4046.86


def load():
    with rasterio.open(DEM_FLAT) as src:
        dem = src.read(1).astype(np.float64)
        tr = src.transform
    with rasterio.open(MASK_TIF) as src:
        mask = src.read(1).astype(bool)
    with rasterio.open(layer_tif("area_2m")) as src:
        area2 = src.read(1)
    with rasterio.open(layer_tif("solid_2m")) as src:
        solid2 = src.read(1)
    from rasterio import open as _o
    with _o("data/lake_mask.tif") as src:
        lake_mask = src.read(1).astype(bool)
    pts = np.load(LAKE_POINTS_NPZ)["points"]
    water = json.loads(META_JSON.read_text())["water_level_m"]
    return dem, tr, mask, area2, solid2, lake_mask, pts, water


def rc_to_xy(tr, r, c):
    return tr.c + (c + 0.5) * tr.a, tr.f + (r + 0.5) * tr.e


def xy_to_rc(tr, x, y):
    return int((tr.f - y) / -tr.e), int((x - tr.c) / tr.a)


def bilinear_vec(dem, tr, xs, ys):
    fc = (xs - tr.c) / tr.a - 0.5
    fr = (tr.f - ys) / -tr.e - 0.5
    r0 = np.clip(np.floor(fr).astype(int), 0, dem.shape[0] - 2)
    c0 = np.clip(np.floor(fc).astype(int), 0, dem.shape[1] - 2)
    dr, dc = fr - r0, fc - c0
    return (dem[r0, c0] * (1 - dr) * (1 - dc) + dem[r0, c0 + 1] * (1 - dr) * dc
            + dem[r0 + 1, c0] * dr * (1 - dc) + dem[r0 + 1, c0 + 1] * dr * dc)


def brute_force_visible(dem, tr, ox, oy, eye_z, pts, water, step=4.0):
    tz = water + OBS_HEIGHT_WATER
    vis = np.zeros(len(pts), dtype=bool)
    for i, (px, py) in enumerate(pts):
        dist = float(np.hypot(px - ox, py - oy))
        n = max(int(dist / step), 2)
        t = np.linspace(0.0, 1.0, n, endpoint=False)[1:]
        d = t * dist
        terr = bilinear_vec(dem, tr, ox + t * (px - ox), oy + t * (py - oy))
        terr_adj = terr - CURV_COEFF * d ** 2 / (2 * R_EARTH)
        target_adj = tz - CURV_COEFF * dist ** 2 / (2 * R_EARTH)
        line = eye_z + t * (target_adj - eye_z)
        vis[i] = bool(np.all(line >= terr_adj - 1e-6))
    return vis


def forward_gdal_visible(tr, ox, oy, pts):
    ds = gdal.Open(str(DEM_FLAT))
    vs = gdal.ViewshedGenerate(
        ds.GetRasterBand(1), "MEM", "", [], ox, oy,
        EYE, OBS_HEIGHT_WATER, 255.0, 0.0, 0.0, 0.0,
        CURV_COEFF, gdal.GVM_Edge, 0.0,
    )
    arr = vs.GetRasterBand(1).ReadAsArray()
    return np.array([arr[xy_to_rc(tr, px, py)] == 255 for px, py in pts])


def dilate(m, n):
    out = m.copy()
    for _ in range(n):
        out = (out | np.roll(out, 1, 0) | np.roll(out, -1, 0)
               | np.roll(out, 1, 1) | np.roll(out, -1, 1))
    return out


def main():
    dem, tr, mask, area2, solid2, lake_mask, pts, water = load()

    # widest-basin center: lake sample point with the most water within 600 m
    d2 = ((pts[:, None, 0] - pts[None, :, 0]) ** 2
          + (pts[:, None, 1] - pts[None, :, 1]) ** 2)
    openness = (d2 < 600.0 ** 2).sum(1)
    bx, by = pts[np.argmax(openness)]
    print(f"widest basin center (UTM): {bx:.0f}, {by:.0f}")

    px_dist2 = None
    rows, cols = np.nonzero(mask)
    xs = tr.c + (cols + 0.5) * tr.a
    ys = tr.f + (rows + 0.5) * tr.e
    near_basin = (xs - bx) ** 2 + (ys - by) ** 2 < 1500.0 ** 2

    shore = dilate(lake_mask, 3) & mask   # land within ~30 m of water
    shore_flat = shore[rows, cols]
    vals = area2[rows, cols]

    cand_shore = np.nonzero(shore_flat & near_basin & (vals > 0))[0]
    cand_hill = np.nonzero(~shore_flat & near_basin)[0]

    tests = []
    q = np.percentile(vals[cand_shore], [20, 50])
    i_lo = cand_shore[np.argmin(np.abs(vals[cand_shore] - q[0]))]
    i_md = cand_shore[np.argmin(np.abs(vals[cand_shore] - q[1]))]
    i_hi = cand_hill[np.argmax(vals[cand_hill])]
    tests = [("shore (20th pct)", i_lo), ("shore (median)", i_md),
             ("uphill max", i_hi)]

    print(f"\n{'site':<18}{'elev':>7}{'accumulated':>13}{'forward GDAL':>14}"
          f"{'brute force':>13}{'solid msr':>11}")
    for name, i in tests:
        r, c = rows[i], cols[i]
        ox, oy = rc_to_xy(tr, r, c)
        acc_ac = vals[i] / ACRE
        fwd = forward_gdal_visible(tr, ox, oy, pts)
        bf = brute_force_visible(dem, tr, ox, oy, dem[r, c] + EYE, pts, water)
        fwd_ac = fwd.sum() * LAKE_SAMPLE_AREA / ACRE
        bf_ac = bf.sum() * LAKE_SAMPLE_AREA / ACRE
        print(f"{name:<18}{dem[r, c]:>6.0f}m{acc_ac:>11.1f}ac{fwd_ac:>12.1f}ac"
              f"{bf_ac:>11.1f}ac{solid2[r, c] * 1000:>11.2f}")
        agree = (fwd == bf).mean() * 100
        print(f"{'':<18}per-point agreement forward-GDAL vs brute-force: "
              f"{agree:.1f}%  ({fwd.sum()} vs {bf.sum()} visible)")

        az = np.degrees(np.arctan2(pts[fwd, 0] - ox, pts[fwd, 1] - oy)) % 360
        hist = np.histogram(az, bins=12, range=(0, 360))[0]
        print(f"{'':<18}azimuth coverage (30-deg bins N->E): {hist.tolist()}")

    print("\nNote: 'accumulated' is the production raster (inverted viewshed);"
          "\n'forward GDAL' and 'brute force' are independent recomputations.")


if __name__ == "__main__":
    sys.exit(main())
