"""Aggregate view metrics per parcel (mean/max over each parcel's analysis cells).

If data/listings.csv exists (columns: address,price,url) the asking prices are
matched to parcels by street address and included in the output for the
lot-finder's view-per-listing-dollar ranking.
"""

import csv
import re
import sys

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize

from common import DATA, LAYERS, MASK_TIF, OUT, PARCELS_RAW, UTM_EPSG, layer_tif

PARCELS_OUT = OUT / "webmap" / "parcels.geojson"
LISTINGS_CSV = DATA / "listings.csv"


def norm_addr(a):
    return re.sub(r"[^A-Z0-9 ]", "", str(a or "").upper()).strip()


def zonal(values, zone_ids, n_zones):
    """Mean and max of `values` per zone id using bincount (zone_ids >= 0)."""
    cnt = np.bincount(zone_ids, minlength=n_zones)
    s = np.bincount(zone_ids, weights=values, minlength=n_zones)
    mean = np.divide(s, cnt, out=np.zeros(n_zones), where=cnt > 0)
    mx = np.zeros(n_zones)
    np.maximum.at(mx, zone_ids, values)
    return mean, mx, cnt


def main():
    parcels = gpd.read_file(PARCELS_RAW).to_crs(UTM_EPSG)
    parcels = parcels[parcels.geometry.notna() & ~parcels.geometry.is_empty].reset_index(drop=True)
    print(f"{len(parcels)} parcels")

    with rasterio.open(MASK_TIF) as src:
        mask = src.read(1).astype(bool)
        transform = src.transform
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
        mean, mx, cnt = zonal(vals, zone_ids, n)
        parcels[f"{name}_mean"] = mean[1:].round(4)
        parcels[f"{name}_max"] = mx[1:].round(4)
    parcels["cells"] = np.bincount(zone_ids, minlength=n)[1:]

    # acres of visible lake reads better than m^2
    for tag in ("2m", "8m"):
        for stat in ("mean", "max"):
            parcels[f"area_{tag}_{stat}_ac"] = (
                parcels[f"area_{tag}_{stat}"] / 4046.86
            ).round(1)

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
        "NFMTTLVL", "NFMLNDVL", "CONSIDR1", "TRADATE", "SDATWEBADR", "cells",
        "LIST_PRICE", "LIST_URL",
        "area_2m_mean_ac", "area_2m_max_ac", "area_8m_mean_ac", "area_8m_max_ac",
        "solid_2m_mean", "solid_2m_max", "solid_8m_mean", "solid_8m_max",
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
