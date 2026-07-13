"""Calculate free-flow drive time from every parcel to configured destinations.

The road graph is downloaded once from OpenStreetMap and cached as GraphML.
Each destination group requires only one reverse multi-source Dijkstra run, so
adding a future geocoded home address is a configuration change rather than a
new routing design.
"""

import json
import sys

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from pyproj import Transformer

from common import DATA, PARCELS_RAW, UTM_EPSG

DESTINATIONS = DATA / "destinations.json"
GRAPH_FILE = DATA / "roads_drive.graphml"
ROUTES_FILE = DATA / "parcel_routes.csv"

# Conservative rural-road defaults fill OSM ways without maxspeed tags.
HIGHWAY_SPEEDS_KPH = {
    "motorway": 105,
    "motorway_link": 55,
    "trunk": 90,
    "trunk_link": 50,
    "primary": 75,
    "primary_link": 45,
    "secondary": 65,
    "secondary_link": 40,
    "tertiary": 55,
    "tertiary_link": 35,
    "residential": 35,
    "living_street": 20,
    "unclassified": 40,
    "service": 20,
}
# Raw edge speed limits are optimistic on winding mountain roads. Three
# cross-check routes against OSRM were consistently 1.37-1.43x slower, so use
# a documented regional calibration rather than presenting speed-limit time.
ROUTING_TIME_FACTOR = 1.40


def load_config():
    cfg = json.loads(DESTINATIONS.read_text())
    if not cfg.get("groups"):
        raise ValueError(f"No destination groups in {DESTINATIONS}")
    return cfg


def graph_bounds(parcels, cfg):
    bounds = parcels.to_crs(4326).total_bounds
    lons = [p["lon"] for g in cfg["groups"] for p in g["points"]]
    lats = [p["lat"] for g in cfg["groups"] for p in g["points"]]
    margin = 0.035
    return (
        min(bounds[0], min(lons)) - margin,
        min(bounds[1], min(lats)) - margin,
        max(bounds[2], max(lons)) + margin,
        max(bounds[3], max(lats)) + margin,
    )


def load_or_download_graph(parcels, cfg):
    if GRAPH_FILE.exists():
        print(f"roads: using cached {GRAPH_FILE}")
        graph = ox.load_graphml(GRAPH_FILE)
    else:
        bbox = graph_bounds(parcels, cfg)
        print(f"roads: downloading OSM drive network for bbox {bbox}")
        ox.settings.requests_timeout = 300
        ox.settings.use_cache = True
        graph = ox.graph_from_bbox(
            bbox=bbox,
            network_type="drive",
            simplify=True,
            retain_all=False,
            truncate_by_edge=True,
        )
        ox.save_graphml(graph, GRAPH_FILE)
        print(
            f"roads: cached {len(graph.nodes):,} nodes / "
            f"{len(graph.edges):,} directed edges"
        )

    graph = ox.routing.add_edge_speeds(
        graph, hwy_speeds=HIGHWAY_SPEEDS_KPH, fallback=35
    )
    graph = ox.routing.add_edge_travel_times(graph)
    return graph


def main():
    cfg = load_config()
    parcels = gpd.read_file(PARCELS_RAW).to_crs(UTM_EPSG)
    parcels = parcels[
        parcels.geometry.notna() & ~parcels.geometry.is_empty
    ].reset_index(drop=True)

    graph = load_or_download_graph(parcels, cfg)
    projected = ox.project_graph(graph, to_crs=f"EPSG:{UTM_EPSG}")

    origins = parcels.geometry.representative_point()
    origin_nodes = ox.distance.nearest_nodes(
        projected, X=origins.x.to_numpy(), Y=origins.y.to_numpy()
    )

    to_utm = Transformer.from_crs(4326, UTM_EPSG, always_xy=True)
    reverse = projected.reverse(copy=False)
    output = pd.DataFrame({"parcel_idx": np.arange(len(parcels), dtype=int)})

    destination_meta = []
    for group in cfg["groups"]:
        coords = [
            to_utm.transform(point["lon"], point["lat"])
            for point in group["points"]
        ]
        target_nodes = list(
            dict.fromkeys(
                ox.distance.nearest_nodes(
                    projected,
                    X=np.array([xy[0] for xy in coords]),
                    Y=np.array([xy[1] for xy in coords]),
                ).tolist()
            )
        )
        lengths = nx.multi_source_dijkstra_path_length(
            reverse, target_nodes, weight="travel_time"
        )
        minutes = np.array(
            [
                lengths.get(node, np.nan) / 60 * ROUTING_TIME_FACTOR
                for node in origin_nodes
            ],
            dtype=float,
        )
        field = f"drive_{group['id']}_min"
        output[field] = np.round(minutes, 1)
        destination_meta.append(
            {
                "id": group["id"],
                "field": field,
                "label": group["label"],
                "points": group["points"],
            }
        )
        finite = minutes[np.isfinite(minutes)]
        print(
            f"{group['label']}: median {np.median(finite):.1f} min, "
            f"range {finite.min():.1f}-{finite.max():.1f} min"
        )

    output.to_csv(ROUTES_FILE, index=False)
    (DATA / "routing_meta.json").write_text(
        json.dumps(
            {
                "source": "OpenStreetMap",
                "measure": "estimated drive time",
                "regional_time_factor": ROUTING_TIME_FACTOR,
                "destinations": destination_meta,
            },
            indent=2,
        )
    )
    print(f"routes: {len(output):,} parcels -> {ROUTES_FILE}")


if __name__ == "__main__":
    sys.exit(main())
