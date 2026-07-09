"""Export the flattened DEM and lake sample points as JS data for the
in-browser point inspector (click a spot -> raytrace which water it sees)."""

import base64
import json
import sys

import numpy as np
import rasterio

from common import (
    CURV_COEFF,
    DEM_FLAT,
    LAKE_POINTS_NPZ,
    LAKE_SAMPLE_AREA,
    META_JSON,
    OBS_HEIGHT_WATER,
    UTM_EPSG,
    WEBMAP,
)


def main():
    with rasterio.open(DEM_FLAT) as src:
        dem = src.read(1)
        tr = src.transform
        h, w = dem.shape

    dm = np.round(dem * 10.0).astype("<u2")  # decimeters, little-endian uint16
    b64 = base64.b64encode(dm.tobytes()).decode()

    meta = json.loads(META_JSON.read_text())
    payload = {
        "w": w, "h": h,
        "x0": tr.c, "y0": tr.f, "res": tr.a,
        "epsg": UTM_EPSG,
        "water": meta["water_level_m"],
        "sampleArea": LAKE_SAMPLE_AREA,
        "targetH": OBS_HEIGHT_WATER,
        "curv": CURV_COEFF,
    }
    (WEBMAP / "dem.js").write_text(
        "var DEMMETA = " + json.dumps(payload) + ";\n"
        + 'var DEMB64 = "' + b64 + '";\n'
    )

    pts = np.load(LAKE_POINTS_NPZ)["points"]
    pts_r = [[round(x, 1), round(y, 1)] for x, y in pts]
    (WEBMAP / "lakepts.js").write_text("var LAKEPTS = " + json.dumps(pts_r) + ";\n")

    print(f"dem.js: {(WEBMAP / 'dem.js').stat().st_size / 1e6:.1f} MB "
          f"({w}x{h} @ {tr.a} m)")
    print(f"lakepts.js: {len(pts_r)} points")


if __name__ == "__main__":
    sys.exit(main())
