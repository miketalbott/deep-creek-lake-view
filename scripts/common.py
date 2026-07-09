"""Shared configuration and helpers for the Deep Creek Lake view analysis."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"
RASTERS = OUT / "rasters"
WEBMAP = OUT / "webmap"

for d in (DATA, RASTERS, WEBMAP):
    d.mkdir(parents=True, exist_ok=True)

# --- CRS / grid ---
UTM_EPSG = 32617          # UTM zone 17N (Deep Creek Lake is ~79.3W)
RES = 10.0                # analysis cell size, meters

# --- analysis parameters ---
BUFFER_M = 1000.0         # analyze land within this distance of the lake shore
LAKE_SAMPLE_SPACING = 50.0  # lake-surface sample point spacing, m (must be multiple of RES)
LAKE_SAMPLE_AREA = LAKE_SAMPLE_SPACING ** 2  # m^2 represented by each lake sample

EYE_HEIGHTS = {"2m": 2.0, "8m": 8.0}  # observer eye height above ground on land
OBS_HEIGHT_WATER = 0.1    # nominal height of the lake sample "target" above water surface
CURV_COEFF = 0.85714      # standard earth curvature + refraction coefficient

# --- data sources ---
OSM_LAKE_RELATION = 2175169  # OSM relation id for Deep Creek Lake
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PARCEL_SERVICE = (
    "https://mdgeodata.md.gov/imap/rest/services/"
    "PlanningCadastre/MD_ParcelBoundaries/MapServer/0/query"
)
DEM_TILE_URL = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/"
    "current/{name}/USGS_13_{name}.tif"
)

# --- files ---
LAKE_GEOJSON = DATA / "lake_utm.geojson"       # lake polygon, EPSG:32617
PARCELS_RAW = DATA / "parcels_raw.geojson"     # parcels, EPSG:4326
DEM_UTM = DATA / "dem_utm.tif"                 # warped DEM, EPSG:32617 @ 10m
DEM_FLAT = DATA / "dem_flat.tif"               # DEM with lake forced to flat water level
MASK_TIF = DATA / "analysis_mask.tif"          # 1 = land cell to evaluate
LAKE_MASK_TIF = DATA / "lake_mask.tif"
LAKE_POINTS_NPZ = DATA / "lake_points.npz"
META_JSON = DATA / "meta.json"

LAYERS = ["area_2m", "area_8m", "solid_2m", "solid_8m"]


def layer_tif(name: str) -> Path:
    return RASTERS / f"{name}.tif"
