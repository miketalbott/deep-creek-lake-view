# Deep Creek Lake — Lake View Analysis

Quantifies **how much of Deep Creek Lake is visible** from every point on land
within 1 km of the shoreline, and aggregates the results per property parcel.
The output is an interactive web map (`output/webmap/index.html`).

## Method

Inverted cumulative viewshed. The lake surface is sampled on a 70 m grid
(~2,900 points, each representing ~1.2 acres of water). For each lake point,
GDAL's viewshed engine computes — over a 10 m bare-earth DEM with the lake
flattened to its pool elevation — the *minimum eye height* every land cell
would need to see that point (earth curvature and refraction included).
Because line of sight is reciprocal, accumulating these gives per land cell:

- **Visible lake area** (m²): sum of lake-sample areas visible from a 2 m
  (ground) or 8 m (second story) eye height.
- **Perceived size** (steradians): each visible lake patch weighted by the
  solid angle it subtends, `A·Δh / (r² + Δh²)^1.5` — approximates how large
  the water actually looks (screen-space area), so near water dominates a
  distant sliver.

Parcel stats (mean over lot, max "best spot") come from zonal statistics of
these rasters over Maryland parcel polygons.

The web map also has a **point inspector**: click anywhere and the browser
raytraces sightlines to all ~5,700 lake sample points over the same DEM
(shipped to the page as packed uint16) and paints visible water blue and
hidden water gray, with the acreage total.

## Lot finder

The map's **Lot finder** scores and ranks parcels (0-100, percentile-based):

- **Rank by**: best view, or view per dollar of SDAT assessed total value,
  assessed land value, or asking price.
- **View blend** slider mixes total visible lake acres with the perceived
  (solid-angle) size; uses the currently selected eye height and each lot's
  best spot.
- Filters: residential only, undeveloped only (no year built), minimum acres.
- Scored parcels are choropleth-colored on the map; the top 15 list zooms to
  each lot. Popups show score, assessed value, and last sale price/year.

**Current listings:** there is no public MLS API, so paste lots you are
watching into `data/listings.csv` (columns `address,price,url`, e.g.
`123 Lake Shore Dr,\$599000,https://...`), then rerun steps 04-05. Matched
parcels get a FOR SALE badge and can be ranked by view per asking dollar.

**Caveat:** bare-earth terrain only — trees and buildings are ignored, so this
is "what the lot *could* see with cleared sightlines," an optimistic bound.

## Data sources (all public, fetched by the scripts)

| Data | Source |
|---|---|
| Terrain | USGS 3DEP 1/3 arc-second (~10 m) DEM |
| Lake polygon | OpenStreetMap relation 2175169 (ODbL) |
| Parcels | Maryland iMAP / MD Planning & SDAT parcel boundaries |

## Running

Requires [uv](https://docs.astral.sh/uv/) and Homebrew GDAL
(`brew install gdal`; the `gdal` Python package is pinned to the same version
in `pyproject.toml`).

```bash
uv sync
uv run python scripts/step01_download.py   # ~500 MB DEM + parcels + lake
uv run python scripts/step02_prepare.py    # warp DEM, masks, lake samples
uv run python scripts/step03_viewshed.py   # cumulative viewshed (~1 min on 8 cores)
uv run python scripts/step04_parcels.py    # per-parcel zonal stats
uv run python scripts/step05_webmap.py     # PNG overlays + map data
uv run python scripts/step06_pointdata.py  # DEM + lake points for the inspector
open output/webmap/index.html              # works from file://, no server needed
```

`scripts/verify.py` re-checks the viewshed math with a forward GDAL viewshed
and an independent numpy raytracer; `node scripts/test_inspector.mjs` checks
the browser inspector against the same reference.

Tuning knobs live in `scripts/common.py` (buffer distance, grid resolution,
lake sample spacing, eye heights). Raw GeoTIFFs for GIS use are in
`output/rasters/` (EPSG:32617): `area_2m/8m` in m² visible,
`solid_2m/8m` in steradians.
