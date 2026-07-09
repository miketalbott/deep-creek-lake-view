"""Download source data: lake polygon (OSM), USGS 3DEP DEM, MD parcel boundaries."""

import json
import math
import sys
import time

import geopandas as gpd
import requests
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

from common import (
    BUFFER_M,
    DATA,
    DEM_TILE_URL,
    LAKE_GEOJSON,
    OSM_LAKE_RELATION,
    OVERPASS_URL,
    PARCEL_SERVICE,
    PARCELS_RAW,
    UTM_EPSG,
)

UA = {"User-Agent": "deep-creek-lake-view/1.0 (personal research)"}


def fetch_lake() -> gpd.GeoDataFrame:
    """Fetch the Deep Creek Lake multipolygon from OSM and save it in UTM."""
    if LAKE_GEOJSON.exists():
        print(f"lake: using cached {LAKE_GEOJSON}")
        return gpd.read_file(LAKE_GEOJSON)

    query = f"[out:json][timeout:120];rel({OSM_LAKE_RELATION});out geom;"
    r = requests.post(OVERPASS_URL, data={"data": query}, headers=UA, timeout=180)
    r.raise_for_status()
    rel = next(e for e in r.json()["elements"] if e["type"] == "relation")

    outer_lines, inner_lines = [], []
    for m in rel["members"]:
        if m["type"] != "way" or "geometry" not in m:
            continue
        coords = [(p["lon"], p["lat"]) for p in m["geometry"]]
        if len(coords) < 2:
            continue
        (outer_lines if m["role"] != "inner" else inner_lines).append(LineString(coords))

    def rings_to_polys(lines):
        return [Polygon(g.exterior) for g in polygonize(unary_union(lines))]

    outers = rings_to_polys(outer_lines)
    inners = rings_to_polys(inner_lines) if inner_lines else []
    lake = unary_union(outers)
    if inners:
        lake = lake.difference(unary_union(inners))

    gdf = gpd.GeoDataFrame({"name": ["Deep Creek Lake"]}, geometry=[lake], crs=4326)
    gdf = gdf.to_crs(UTM_EPSG)
    gdf.to_file(LAKE_GEOJSON, driver="GeoJSON")
    print(f"lake: {gdf.geometry.area.sum() / 4046.86:,.0f} acres -> {LAKE_GEOJSON}")
    return gdf


def aoi_bounds_4326(lake_utm: gpd.GeoDataFrame):
    """Analysis AOI (lake + buffer + margin) as a lon/lat bbox."""
    aoi = lake_utm.geometry.buffer(BUFFER_M + 300).to_crs(4326)
    return aoi.total_bounds  # minx, miny, maxx, maxy


def fetch_dem(bounds4326) -> list:
    minx, miny, maxx, maxy = bounds4326
    tiles = set()
    for lon in range(math.floor(minx), math.ceil(maxx)):
        for lat in range(math.floor(miny), math.ceil(maxy)):
            # tiles are named by their NW corner: n{lat+1}w{abs(lon)}
            tiles.add(f"n{lat + 1:02d}w{abs(lon):03d}")
    paths = []
    for name in sorted(tiles):
        dst = DATA / f"USGS_13_{name}.tif"
        paths.append(dst)
        if dst.exists():
            print(f"dem: using cached {dst.name}")
            continue
        url = DEM_TILE_URL.format(name=name)
        print(f"dem: downloading {url}")
        with requests.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            tmp = dst.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.rename(dst)
        print(f"dem: saved {dst.name} ({dst.stat().st_size / 1e6:.0f} MB)")
    return paths


FIELDS = [
    "ACCTID", "ADDRESS", "CITY", "ZIPCODE", "DESCLU", "ACRES",
    "YEARBLT", "SQFTSTRC", "NFMLNDVL", "NFMIMPVL", "NFMTTLVL", "SDATWEBADR",
    "CONSIDR1", "TRADATE", "RESITYP", "OOI",
]


def fetch_parcels(bounds4326):
    if PARCELS_RAW.exists():
        print(f"parcels: using cached {PARCELS_RAW}")
        return
    minx, miny, maxx, maxy = bounds4326
    base = {
        "where": "1=1",
        "geometry": f"{minx},{miny},{maxx},{maxy}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(FIELDS),
        "outSR": 4326,
        "f": "geojson",
        "orderByFields": "OBJECTID",
        "resultRecordCount": 1000,
        "geometryPrecision": 6,
    }
    features = []
    offset = 0
    while True:
        params = dict(base, resultOffset=offset)
        for attempt in range(4):
            try:
                r = requests.get(PARCEL_SERVICE, params=params, headers=UA, timeout=180)
                r.raise_for_status()
                page = r.json()
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    raise
                print(f"parcels: retry after error: {e}")
                time.sleep(5)
        feats = page.get("features", [])
        features.extend(feats)
        print(f"parcels: fetched {len(features)} so far")
        if len(feats) < 1000:
            break
        offset += 1000
    fc = {"type": "FeatureCollection", "features": features}
    PARCELS_RAW.write_text(json.dumps(fc))
    print(f"parcels: {len(features)} parcels -> {PARCELS_RAW}")


def main():
    lake = fetch_lake()
    bounds = aoi_bounds_4326(lake)
    print(f"AOI (lon/lat): {bounds}")
    fetch_dem(bounds)
    fetch_parcels(bounds)
    print("download complete")


if __name__ == "__main__":
    sys.exit(main())
