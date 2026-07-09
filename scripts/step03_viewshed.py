"""Cumulative viewshed: accumulate visible-lake-area and perceived-size metrics.

For each lake-surface sample point we run one GDAL viewshed in
"minimum target height above ground" mode over the flattened DEM. Line of
sight is reciprocal, so a land cell whose required height is <= 2 m can see
that patch of lake from a 2 m eye height (and likewise 8 m). Each visible
lake sample contributes:

  area layers:  the lake area it represents (LAKE_SAMPLE_AREA, m^2)
  solid layers: its solid angle as seen from the cell,
                A * dh / (r^2 + dh^2)^1.5   [steradians]
                where dh = eye elevation - water level, r = horizontal distance.
"""

import argparse
import json
import multiprocessing as mp
import sys
import time

import numpy as np
import rasterio
from osgeo import gdal

from common import (
    CURV_COEFF,
    DEM_FLAT,
    EYE_HEIGHTS,
    LAKE_POINTS_NPZ,
    LAKE_SAMPLE_AREA,
    LAKE_SAMPLE_SPACING,
    LAYERS,
    MASK_TIF,
    META_JSON,
    OBS_HEIGHT_WATER,
    layer_tif,
)

gdal.UseExceptions()

_state = {}


def _init_worker():
    """Load shared inputs once per worker process."""
    with rasterio.open(MASK_TIF) as src:
        mask = src.read(1).astype(bool)
        transform = src.transform
    with rasterio.open(DEM_FLAT) as src:
        dem = src.read(1)
    meta = json.loads(META_JSON.read_text())

    rows, cols = np.nonzero(mask)
    xs = transform.c + (cols + 0.5) * transform.a
    ys = transform.f + (rows + 0.5) * transform.e

    _state.update(
        mask=mask,
        flat_idx=np.flatnonzero(mask.ravel()),
        xs=xs.astype(np.float64),
        ys=ys.astype(np.float64),
        ground=dem[rows, cols].astype(np.float64),
        water=float(meta["water_level_m"]),
        ds=gdal.Open(str(DEM_FLAT)),
    )


def _process_chunk(points: np.ndarray) -> dict:
    s = _state
    n = len(s["xs"])
    acc = {name: np.zeros(n, dtype=np.float64) for name in LAYERS}

    for px, py in points:
        vs_ds = gdal.ViewshedGenerate(
            s["ds"].GetRasterBand(1),
            "MEM",
            "",
            [],
            px,
            py,
            OBS_HEIGHT_WATER,
            0.0,          # targetHeight must be 0 in height modes
            255.0,
            0.0,
            -1.0,         # outOfRangeVal
            -2.0,         # noDataVal
            CURV_COEFF,
            gdal.GVM_Edge,
            0.0,          # maxDistance: unlimited
            heightMode=gdal.GVOT_MIN_TARGET_HEIGHT_FROM_GROUND,
        )
        req = vs_ds.GetRasterBand(1).ReadAsArray().ravel()[s["flat_idx"]]
        vs_ds = None

        valid = req >= 0.0
        r2 = (s["xs"] - px) ** 2 + (s["ys"] - py) ** 2
        # the point approximation of solid angle diverges for samples closer
        # than the sample spacing; clamp to keep shoreline cells finite
        np.maximum(r2, (LAKE_SAMPLE_SPACING / 2.0) ** 2, out=r2)
        for tag, eye in EYE_HEIGHTS.items():
            vis = valid & (req <= eye)
            acc[f"area_{tag}"][vis] += LAKE_SAMPLE_AREA
            dh = s["ground"][vis] + eye - s["water"]
            np.maximum(dh, 0.5, out=dh)
            omega = LAKE_SAMPLE_AREA * dh / (r2[vis] + dh * dh) ** 1.5
            acc[f"solid_{tag}"][vis] += omega
    return acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only process N lake points (smoke test)")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    args = ap.parse_args()

    points = np.load(LAKE_POINTS_NPZ)["points"]
    if args.limit:
        step = max(1, len(points) // args.limit)
        points = points[::step][: args.limit]
    print(f"processing {len(points)} lake points with {args.workers} workers")

    chunks = np.array_split(points, args.workers * 8)
    chunks = [c for c in chunks if len(c)]

    t0 = time.time()
    totals = None
    done = 0
    with mp.get_context("spawn").Pool(args.workers, initializer=_init_worker) as pool:
        for acc in pool.imap_unordered(_process_chunk, chunks):
            if totals is None:
                totals = acc
            else:
                for k in totals:
                    totals[k] += acc[k]
            done += 1
            el = time.time() - t0
            print(f"  chunk {done}/{len(chunks)} done, {el:.0f}s elapsed, "
                  f"eta {el / done * (len(chunks) - done):.0f}s", flush=True)

    with rasterio.open(MASK_TIF) as src:
        mask = src.read(1).astype(bool)
        profile = src.profile
    profile.update(dtype="float32", nodata=-9999.0, compress="deflate", tiled=True)

    for name in LAYERS:
        grid = np.full(mask.shape, -9999.0, dtype=np.float32)
        grid[mask] = totals[name].astype(np.float32)
        with rasterio.open(layer_tif(name), "w", **profile) as dst:
            dst.write(grid, 1)
        v = totals[name]
        print(f"{name}: max {v.max():,.4g}  mean {v.mean():,.4g}  "
              f"nonzero {(v > 0).mean() * 100:.1f}%  -> {layer_tif(name).name}")
    print(f"viewshed complete in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
