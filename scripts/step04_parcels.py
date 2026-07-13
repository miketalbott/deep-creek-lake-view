"""Aggregate view, terrain, shoreline, value, route, and listing metrics.

If data/listings.csv exists (columns: address,price,url) the asking prices are
matched to parcels by street address and included in the output for the
weighted lot finder.
"""

import csv
import re
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize

from common import (
    DATA,
    DEM_UTM,
    LAKE_GEOJSON,
    LAKE_MASK_TIF,
    LAYERS,
    MASK_TIF,
    OUT,
    PARCELS_RAW,
    RES,
    UTM_EPSG,
    layer_tif,
)

PARCELS_OUT = OUT / "webmap" / "parcels.geojson"
LISTINGS_CSV = DATA / "listings.csv"
ROUTES_CSV = DATA / "parcel_routes.csv"
BUILDABLE_SLOPE_PCT = 15.0


def norm_addr(a):
    return re.sub(r"[^A-Z0-9 ]", "", str(a or "").upper()).strip()


def zonal(values, zone_ids, n_zones, quantile=None):
    """Mean, max, and optional quantile of values per zone id."""
    cnt = np.bincount(zone_ids, minlength=n_zones)
    s = np.bincount(zone_ids, weights=values, minlength=n_zones)
    mean = np.divide(s, cnt, out=np.zeros(n_zones), where=cnt > 0)
    mx = np.zeros(n_zones)
    np.maximum.at(mx, zone_ids, values)
    q = np.zeros(n_zones)
    if quantile is not None:
        order = np.argsort(zone_ids, kind="stable")
        sorted_values = values[order]
        ends = np.cumsum(cnt)
        for zone in np.flatnonzero(cnt):
            start = 0 if zone == 0 else ends[zone - 1]
            q[zone] = np.quantile(sorted_values[start : ends[zone]], quantile)
    return mean, mx, q, cnt


def main():
    parcels = gpd.read_file(PARCELS_RAW).to_crs(UTM_EPSG)
    parcels = parcels[parcels.geometry.notna() & ~parcels.geometry.is_empty].reset_index(drop=True)
    print(f"{len(parcels)} parcels")

    with rasterio.open(MASK_TIF) as src:
        mask = src.read(1).astype(bool)
        transform = src.transform
    with rasterio.open(LAKE_MASK_TIF) as src:
        lake_mask = src.read(1).astype(bool)
    shape = mask.shape

    # rasterize parcel index (background = 0, parcel i -> i+1)
    zones = rasterize(
        ((geom, i + 1) for i, geom in enumerate(parcels.geometry)),
        out_shape=shape, transform=transform, dtype=np.int32, all_touched=False,
    )
    # small parcels can vanish at 10 m; re-burn with all_touched for those only
    covered = np.unique(zones)
    missing = sorted(set(range(1, len(parcels) + 1)) - set(covered.tolist()))
    if missing:
        patch = rasterize(
            ((parcels.geometry[i - 1], i) for i in missing),
            out_shape=shape, transform=transform, dtype=np.int32, all_touched=True,
        )
        fill = (zones == 0) & (patch > 0)
        zones[fill] = patch[fill]
        print(f"re-burned {len(missing)} sub-cell parcels with all_touched")

    inside = mask & (zones > 0)
    zone_ids = zones[inside]
    n = len(parcels) + 1

    for name in LAYERS:
        with rasterio.open(layer_tif(name)) as src:
            vals = src.read(1)[inside].astype(np.float64)
        mean, mx, p90, cnt = zonal(vals, zone_ids, n, quantile=0.90)
        parcels[f"{name}_mean"] = mean[1:].round(4)
        parcels[f"{name}_max"] = mx[1:].round(4)
        parcels[f"{name}_p90"] = p90[1:].round(4)
    parcels["cells"] = np.bincount(zone_ids, minlength=n)[1:]

    # acres of visible lake reads better than m^2
    for tag in ("2m", "8m"):
        for stat in ("mean", "max", "p90"):
            parcels[f"area_{tag}_{stat}_ac"] = (
                parcels[f"area_{tag}_{stat}"] / 4046.86
            ).round(1)

    # Terrain buildability: acres and fraction at <=15% grade. This is a
    # screening metric, not a substitute for a survey/geotechnical review.
    with rasterio.open(DEM_UTM) as src:
        dem = src.read(1).astype(np.float64)
    dz_dy, dz_dx = np.gradient(dem, RES, RES)
    slope_pct = np.hypot(dz_dx, dz_dy) * 100
    terrain_inside = (zones > 0) & ~lake_mask & np.isfinite(slope_pct)
    terrain_zone_ids = zones[terrain_inside]
    terrain_count = np.bincount(terrain_zone_ids, minlength=n)
    usable_count = np.bincount(
        terrain_zone_ids,
        weights=(slope_pct[terrain_inside] <= BUILDABLE_SLOPE_PCT),
        minlength=n,
    )
    parcels["usable_acres"] = (usable_count[1:] * RES * RES / 4046.86).round(2)
    parcels["usable_pct"] = (
        np.divide(
            usable_count,
            terrain_count,
            out=np.zeros(n),
            where=terrain_count > 0,
        )[1:]
        * 100
    ).round(1)

    # Geometric distance is useful because a large view is not the same as
    # direct water access. Treat <=15 m as approximately waterfront to absorb
    # minor parcel/lake boundary misalignment.
    lake = gpd.read_file(LAKE_GEOJSON).to_crs(UTM_EPSG).geometry.union_all()
    parcels["shore_dist_m"] = parcels.geometry.distance(lake).round(1)
    parcels["waterfront"] = parcels["shore_dist_m"] <= 15

    # Route fields are generated separately because the OSM road download is
    # slow but reusable. The row index is stable after the same geometry filter.
    route_fields = []
    if ROUTES_CSV.exists():
        routes = pd.read_csv(ROUTES_CSV).set_index("parcel_idx")
        if len(routes) != len(parcels):
            raise ValueError(
                f"{ROUTES_CSV} has {len(routes)} rows; expected {len(parcels)}. "
                "Rerun scripts/step04_routes.py."
            )
        route_fields = [c for c in routes.columns if c.startswith("drive_")]
        for field in route_fields:
            parcels[field] = routes[field].to_numpy()
    else:
        print(f"routes: {ROUTES_CSV} missing; route metrics omitted")

    # merge listings by normalized street address, if provided
    parcels["LIST_PRICE"] = np.nan
    parcels["LIST_URL"] = None
    if LISTINGS_CSV.exists():
        addr_index = {}
        for i, a in enumerate(parcels["ADDRESS"]):
            addr_index.setdefault(norm_addr(a), []).append(i)
        n_match = 0
        with open(LISTINGS_CSV) as f:
            for row in csv.DictReader(f):
                key = norm_addr(row.get("address"))
                hits = addr_index.get(key, []) if key else []
                for i in hits:
                    price = re.sub(r"[^0-9.]", "", str(row.get("price", "")))
                    parcels.loc[i, "LIST_PRICE"] = float(price) if price else np.nan
                    parcels.loc[i, "LIST_URL"] = row.get("url") or None
                n_match += bool(hits)
                if not hits:
                    print(f"listings: NO PARCEL MATCH for {row.get('address')!r}")
        print(f"listings: matched {n_match} of file rows")

    out = parcels.to_crs(4326)
    # trim to what the webmap needs
    keep = [
        "ACCTID", "ADDRESS", "CITY", "DESCLU", "ACRES", "YEARBLT",
        "NFMTTLVL", "NFMLNDVL", "NFMIMPVL", "CONSIDR1", "TRADATE",
        "SDATWEBADR", "cells",
        "LIST_PRICE", "LIST_URL",
        "area_2m_mean_ac", "area_2m_max_ac", "area_2m_p90_ac",
        "area_8m_mean_ac", "area_8m_max_ac", "area_8m_p90_ac",
        "solid_2m_mean", "solid_2m_max", "solid_2m_p90",
        "solid_8m_mean", "solid_8m_max", "solid_8m_p90",
        "usable_acres", "usable_pct", "shore_dist_m", "waterfront",
        *route_fields,
        "geometry",
    ]
    out = out[keep]
    out["geometry"] = out.geometry.simplify(0.00002)  # ~2 m
    PARCELS_OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(PARCELS_OUT, driver="GeoJSON", coordinate_precision=6)
    print(f"wrote {PARCELS_OUT} ({PARCELS_OUT.stat().st_size / 1e6:.1f} MB)")

    top = out.nlargest(5, "area_2m_max_ac")[["ADDRESS", "area_2m_max_ac", "ACRES"]]
    print("top parcels by max visible lake (2 m):")
    print(top.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
