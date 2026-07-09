"""Prepare analysis rasters: warp DEM to UTM, flatten lake, build masks and lake sample points."""

import json
import math
import sys

import geopandas as gpd
import numpy as np
import rasterio
from osgeo import gdal
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import Point
from shapely.prepared import prep

from common import (
    BUFFER_M,
    DATA,
    DEM_FLAT,
    DEM_UTM,
    LAKE_GEOJSON,
    LAKE_MASK_TIF,
    LAKE_POINTS_NPZ,
    LAKE_SAMPLE_SPACING,
    MASK_TIF,
    META_JSON,
    RES,
    UTM_EPSG,
)

gdal.UseExceptions()

EDGE_MARGIN = 300.0  # extra raster margin beyond the analysis buffer


def snap(v, up=False):
    return (math.ceil if up else math.floor)(v / RES) * RES


def warp_dem(bounds):
    tiles = sorted(str(p) for p in DATA.glob("USGS_13_*.tif"))
    assert tiles, "no DEM tiles downloaded"
    print(f"warping {len(tiles)} DEM tile(s) to UTM {RES} m ...")
    gdal.Warp(
        str(DEM_UTM),
        tiles,
        dstSRS=f"EPSG:{UTM_EPSG}",
        outputBounds=bounds,
        xRes=RES,
        yRes=RES,
        resampleAlg="bilinear",
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES"],
    )


def main():
    lake = gpd.read_file(LAKE_GEOJSON)
    lake_geom = lake.geometry.union_all()

    b = lake.buffer(BUFFER_M + EDGE_MARGIN).total_bounds
    bounds = (snap(b[0]), snap(b[1]), snap(b[2], up=True), snap(b[3], up=True))
    warp_dem(bounds)

    with rasterio.open(DEM_UTM) as src:
        dem = src.read(1).astype(np.float32)
        transform = src.transform
        profile = src.profile
    h, w = dem.shape
    print(f"grid: {w} x {h} cells @ {RES} m")

    lake_mask = rasterize(
        [(lake_geom, 1)], out_shape=(h, w), transform=transform, dtype=np.uint8
    ).astype(bool)

    water_level = float(np.median(dem[lake_mask]))
    print(f"water level (DEM median over lake): {water_level:.2f} m "
          f"({water_level * 3.28084:.0f} ft)")
    dem_flat = dem.copy()
    dem_flat[lake_mask] = water_level

    buf_mask = rasterize(
        [(lake_geom.buffer(BUFFER_M), 1)], out_shape=(h, w), transform=transform,
        dtype=np.uint8,
    ).astype(bool)
    mask = buf_mask & ~lake_mask
    print(f"analysis cells: {mask.sum():,} ({mask.sum() * RES * RES / 1e6:.1f} km^2)")

    # lake-surface sample points on a LAKE_SAMPLE_SPACING grid, pulled back from shore
    inner = lake_geom.buffer(-RES * 1.5)
    prepared = prep(inner)
    x0, y0, x1, y1 = inner.bounds
    xs = np.arange(math.floor(x0), x1, LAKE_SAMPLE_SPACING)
    ys = np.arange(math.floor(y0), y1, LAKE_SAMPLE_SPACING)
    pts = []
    for y in ys:
        for x in xs:
            if not prepared.contains(Point(x, y)):
                continue
            col = int((x - transform.c) / RES)
            row = int((transform.f - y) / RES)
            if 0 <= row < h and 0 <= col < w and lake_mask[row, col]:
                # snap to cell center so the observer sits exactly on a lake cell
                cx = transform.c + (col + 0.5) * RES
                cy = transform.f - (row + 0.5) * RES
                pts.append((cx, cy))
    pts = np.array(pts, dtype=np.float64)
    print(f"lake sample points: {len(pts)} "
          f"(each represents {LAKE_SAMPLE_SPACING ** 2 / 4046.86:.2f} acres)")

    prof8 = dict(profile, dtype="uint8", nodata=None, compress="deflate")
    with rasterio.open(MASK_TIF, "w", **prof8) as dst:
        dst.write(mask.astype(np.uint8), 1)
    with rasterio.open(LAKE_MASK_TIF, "w", **prof8) as dst:
        dst.write(lake_mask.astype(np.uint8), 1)
    proff = dict(profile, dtype="float32", compress="deflate")
    with rasterio.open(DEM_FLAT, "w", **proff) as dst:
        dst.write(dem_flat, 1)
    np.savez(LAKE_POINTS_NPZ, points=pts)
    META_JSON.write_text(json.dumps({
        "water_level_m": water_level,
        "bounds_utm": bounds,
        "width": w,
        "height": h,
        "n_lake_points": len(pts),
        "n_analysis_cells": int(mask.sum()),
    }, indent=2))
    print("prepare complete")


if __name__ == "__main__":
    sys.exit(main())
