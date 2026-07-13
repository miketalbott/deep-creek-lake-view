"""Render heatmap PNG overlays (Web Mercator) and assemble the interactive map data."""

import json
import sys

import geopandas as gpd
import numpy as np
import rasterio
from matplotlib import colormaps
from osgeo import gdal
from PIL import Image
from pyproj import Transformer

from common import DATA, LAKE_GEOJSON, LAYERS, WEBMAP, layer_tif

gdal.UseExceptions()

CMAP = colormaps["turbo"]


def warp_to_3857(name: str):
    dst = DATA / f"{name}_3857.tif"
    gdal.Warp(
        str(dst), str(layer_tif(name)),
        dstSRS="EPSG:3857", xRes=12, yRes=12,
        resampleAlg="bilinear", dstNodata=-9999.0,
    )
    return dst


def colorize(name: str, arr: np.ndarray, valid: np.ndarray, vmax: float):
    """Map values to turbo colormap with sqrt stretch; 0 -> transparent."""
    norm = np.zeros_like(arr, dtype=np.float64)
    norm[valid] = np.sqrt(np.clip(arr[valid] / vmax, 0, 1))
    rgba = (CMAP(norm) * 255).astype(np.uint8)
    # ramp alpha in quickly so tiny-but-nonzero views are still visible
    alpha = np.zeros(arr.shape, dtype=np.uint8)
    a = np.clip(np.sqrt(norm) * 255, 0, 255).astype(np.uint8)
    nonzero = valid & (arr > 0)
    alpha[nonzero] = np.maximum(a[nonzero], 90)
    rgba[..., 3] = alpha
    img = Image.fromarray(rgba, "RGBA")
    out = WEBMAP / f"{name}.png"
    img.save(out, optimize=True)
    return out


def main():
    WEBMAP.mkdir(parents=True, exist_ok=True)
    to4326 = Transformer.from_crs(3857, 4326, always_xy=True)
    config = {"layers": {}}

    # load all layers first so both eye heights share one scale per metric,
    # making the 2 m vs 8 m toggle directly comparable
    loaded = {}
    for name in LAYERS:
        warped = warp_to_3857(name)
        with rasterio.open(warped) as src:
            arr = src.read(1).astype(np.float64)
            b = src.bounds
        loaded[name] = (arr, arr > -9998.0, b)

    vmax_by_metric = {}
    for metric in ("area", "solid"):
        p98s = []
        for name in LAYERS:
            if not name.startswith(metric):
                continue
            arr, valid, _ = loaded[name]
            pos = arr[valid & (arr > 0)]
            if len(pos):
                p98s.append(float(np.percentile(pos, 98)))
        vmax_by_metric[metric] = max(p98s) if p98s else 1.0

    for name in LAYERS:
        arr, valid, b = loaded[name]
        vmax = vmax_by_metric[name.split("_")[0]]
        colorize(name, arr, valid, vmax)

        w, s = to4326.transform(b.left, b.bottom)
        e, n = to4326.transform(b.right, b.top)
        config["layers"][name] = {
            "png": f"{name}.png",
            "bounds": [[s, w], [n, e]],
            "vmax": vmax,
        }
        print(f"{name}: vmax(P98)={vmax:,.4g}")

    lake = gpd.read_file(LAKE_GEOJSON).to_crs(4326)
    lake.to_file(WEBMAP / "lake.geojson", driver="GeoJSON")

    # legend gradient stops (sqrt stretch, turbo)
    stops = [
        {"frac": f, "color": [int(c * 255) for c in CMAP(np.sqrt(f))[:3]]}
        for f in np.linspace(0, 1, 24)
    ]
    config["legend_stops"] = stops
    routing_meta = DATA / "routing_meta.json"
    config["routing"] = (
        json.loads(routing_meta.read_text())
        if routing_meta.exists()
        else {"destinations": []}
    )

    (WEBMAP / "config.js").write_text("var CONFIG = " + json.dumps(config) + ";\n")

    # wrap data as JS so the map works from file:// without a server
    pj = (WEBMAP / "parcels.geojson").read_text()
    (WEBMAP / "parcels.js").write_text("var PARCELS = " + pj + ";\n")
    lj = (WEBMAP / "lake.geojson").read_text()
    (WEBMAP / "lake.js").write_text("var LAKE = " + lj + ";\n")
    print(f"webmap assets in {WEBMAP}")


if __name__ == "__main__":
    sys.exit(main())
