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

Parcel stats come from zonal statistics over Maryland parcel polygons. Scoring
uses the 90th percentile (P90) rather than a single best pixel: it represents
a strong view available across at least 10% of the sampled parcel cells and is
less vulnerable to one-cell artifacts.

The web map also has a **point inspector**: click anywhere and the browser
raytraces sightlines to all ~5,700 lake sample points over the same DEM
(shipped to the page as packed uint16) and paints visible water blue and
hidden water gray, with the acreage total.

## Lot finder

The map's **Lot finder** combines independently weighted 0-100 regional
percentiles:

- View: visible lake area and perceived (solid-angle) size, kept separate.
- Property: lot acres, usable acres at no more than 15% slope, and distance to
  the lake (approximately waterfront within 15 m).
- Value: lower assessed land value, higher assessed improvement value, and
  lower asking price when a matched listing exists.
- Access: estimated drive time to I-68 eastbound at Keysers Ridge, Honi Honi,
  Wisp, the nearer of two full grocery stores, and Garrett Regional Medical
  Center.

The final score is `sum(metric percentile × weight) / sum(weights)`. Reference
percentiles are fixed across the regional parcel set, so filtering does not
renormalize the scores. Sliders update a live ranking after the first run, and
presets provide Balanced, View First, Vacant Land, Existing Home, and Access
starting points. Popups show the raw metrics and current score contribution.

Drive times use a cached OpenStreetMap road graph with a regional mountain-road
calibration. They are planning estimates, not live-traffic ETAs. Destinations
live in `data/destinations.json`; its generic grouped-point format is also the
extension point for a future geocoded home-address destination.

**Current listings:** there is no public MLS API, so paste lots you are
watching into `data/listings.csv` (columns `address,price,url`, e.g.
`123 Lake Shore Dr,\$599000,https://...`), then rerun the parcel and webmap
steps. Matched parcels get a FOR SALE badge and can use asking-price
affordability as a weighted metric.

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
uv run python scripts/step04_routes.py     # OSM road graph + drive times
uv run python scripts/step04_parcels.py    # parcel view/property/value metrics
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
