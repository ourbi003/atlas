from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

import urllib.error
import urllib.request
from urllib.parse import urlencode

from atlas.config import CFG
from atlas.utils.geo import drop_empty_geometries, require_crs
from atlas.utils.io import ensure_dir, read_json, write_json


OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


def _bbox_from_dim_tracts(dim_tracts_path: Path) -> tuple[float, float, float, float]:
    """
    Return (south, west, north, east) bbox in WGS84 for Overpass.

    Overpass bbox order: (S, W, N, E) = (min_lat, min_lon, max_lat, max_lon)
    """
    tracts_gdf = gpd.read_file(dim_tracts_path)
    require_crs(tracts_gdf, name="dim_tracts")

    wgs = tracts_gdf.to_crs("EPSG:4326")
    minx, miny, maxx, maxy = wgs.total_bounds
    south, west, north, east = float(miny), float(minx), float(maxy), float(maxx)
    return south, west, north, east


def _osm_mode_suffix(*, include_ways: bool, include_relations: bool) -> str:
    """
    Build a stable suffix for raw OSM cache files so different query modes do not collide.

    Examples:
    - nodes only              -> nodes
    - nodes + ways            -> nodes_ways
    - nodes + ways + relations -> nodes_ways_relations
    - nodes + relations       -> nodes_relations
    """
    parts = ["nodes"]
    if include_ways:
        parts.append("ways")
    if include_relations:
        parts.append("relations")
    return "_".join(parts)


def _build_overpass_query(
    tag_key: str,
    tag_value: str,
    bbox: tuple[float, float, float, float],
    *,
    include_ways: bool = False,
    include_relations: bool = False,
    query_timeout_s: int = 180,
) -> str:
    """
    Build an Overpass QL query for a single tag key/value within a bbox.

    Default (MVP): nodes only, for speed/reliability on larger regions.
    Optional upgrades:
    - include_ways=True
    - include_relations=True

    When ways/relations are included, use `out center` so Overpass returns a centroid-like
    point (`center.lon`, `center.lat`) that can be normalized into the point-based schema.
    """
    south, west, north, east = bbox

    clauses = [
        f'  node["{tag_key}"="{tag_value}"]({south},{west},{north},{east});',
    ]
    if include_ways:
        clauses.append(f'  way["{tag_key}"="{tag_value}"]({south},{west},{north},{east});')
    if include_relations:
        clauses.append(f'  relation["{tag_key}"="{tag_value}"]({south},{west},{north},{east});')

    out_clause = "out center qt;" if (include_ways or include_relations) else "out body qt;"
    clause_block = "\n".join(clauses)

    return f"""
[out:json][timeout:{int(query_timeout_s)}];
(
{clause_block}
);
{out_clause}
""".strip()


def _overpass_fetch(
    query: str,
    *,
    timeout_s: int = 180,
    retries: int = 3,
) -> dict[str, Any]:
    """
    POST an Overpass QL query and return parsed JSON.

    Adds endpoint fallback: if one Overpass instance is overloaded (504/429),
    try the next.
    """
    payload = urlencode({"data": query}).encode("utf-8")
    headers = {
        "User-Agent": "Atlas/0.1 (educational resume project)",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    last_err: Exception | None = None

    for base_url in OVERPASS_URLS:
        req = urllib.request.Request(base_url, data=payload, headers=headers, method="POST")

        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    raw = resp.read()
                return json.loads(raw.decode("utf-8"))

            except urllib.error.HTTPError as e:
                last_err = e
                status = getattr(e, "code", None)

                # If overloaded/rate-limited, retry a couple times on this endpoint
                if status in {429, 500, 502, 503, 504} and attempt < retries:
                    time.sleep(1.5 * attempt)
                    continue

                # If still failing with overload-like errors, move to next endpoint
                if status in {429, 500, 502, 503, 504}:
                    break

                snippet = ""
                try:
                    body_bytes = e.read()
                    snippet = body_bytes[:300].decode("utf-8", errors="ignore")
                except (OSError, ValueError, UnicodeDecodeError, AttributeError):
                    pass

                raise RuntimeError(f"Overpass HTTPError {status} at {base_url}. Snippet: {snippet}") from e

            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5 * attempt)
                    continue
                # give up on this endpoint and try next
                break

    raise RuntimeError(f"Overpass request failed across endpoints: {OVERPASS_URLS}") from last_err


def _elements_to_rows(elements: list[dict[str, Any]], *, category: str) -> list[dict[str, Any]]:
    """
    Normalize Overpass 'elements' into flat row dicts with lon/lat.

    Supported:
    - nodes: use element['lon'], element['lat']
    - ways: use element['center']['lon'], element['center']['lat']
    - relations: use element['center']['lon'], element['center']['lat']

    Note:
    - ways/relations only have `center` if the query uses `out center`.
    """
    rows: list[dict[str, Any]] = []
    for el in elements:
        el_type = el.get("type")
        el_id = el.get("id")
        tags = el.get("tags") or {}

        lon = lat = None
        if el_type == "node":
            lon = el.get("lon")
            lat = el.get("lat")
        elif el_type in {"way", "relation"}:
            center = el.get("center") or {}
            lon = center.get("lon")
            lat = center.get("lat")

        if lon is None or lat is None:
            continue

        name = tags.get("name")

        rows.append(
            {
                "category": category,
                "osm_type": str(el_type) if el_type is not None else None,
                "osm_id": int(el_id) if el_id is not None else None,
                "osm_uid": f"{el_type}/{el_id}" if el_type is not None and el_id is not None else None,
                "name": name,
                "lon": float(lon),
                "lat": float(lat),
                "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
            }
        )
    return rows


def ingest_osm_amenities(
    *,
    force_fetch: bool = False,
    timeout_s: int = 180,
    include_ways: bool = False,
    include_relations: bool = False,
) -> gpd.GeoDataFrame:
    """
    Ingest OSM amenities (MVP categories) for the configured tract bounding box.

    Inputs:
    - data/curated/.../dim_tracts.geojson

    Outputs:
    - data/raw/.../osm_amenities_raw_<mode>.json
    - data/curated/.../fact_amenities.geojson (EPSG:4326)

    Modes:
    - Default: nodes only (fastest / most reliable)
    - Optional: include ways and/or relations using `out center`
    """
    ensure_dir(CFG.raw_dir)
    ensure_dir(CFG.curated_dir)

    dim_tracts_path = CFG.curated_dir / "dim_tracts.geojson"
    if not dim_tracts_path.exists():
        raise FileNotFoundError(
            f"Missing {dim_tracts_path}. Run ingest_tiger.py first to generate dim_tracts.geojson."
        )

    bbox = _bbox_from_dim_tracts(dim_tracts_path)

    mode_suffix = _osm_mode_suffix(include_ways=include_ways, include_relations=include_relations)
    raw_path = CFG.raw_dir / f"osm_amenities_raw_{mode_suffix}.json"
    curated_path = CFG.curated_dir / "fact_amenities.geojson"

    if raw_path.exists() and not force_fetch:
        raw = read_json(raw_path)
    else:
        results: dict[str, Any] = {
            "meta": {
                "bbox_south_west_north_east": bbox,
                "overpass_urls": OVERPASS_URLS,
                "categories": list(CFG.osm_categories.keys()),
                "osm_element_mode": {
                    "nodes": True,
                    "ways": bool(include_ways),
                    "relations": bool(include_relations),
                },
                "raw_cache_file": str(raw_path),
            },
            "by_category": {},
        }

        for category, tag_map in CFG.osm_categories.items():
            if len(tag_map) != 1:
                raise ValueError(
                    f"Category '{category}' must map to exactly one tag key/value in MVP. Got: {tag_map}"
                )

            tag_key, tag_value = next(iter(tag_map.items()))
            q = _build_overpass_query(
                tag_key,
                tag_value,
                bbox,
                include_ways=include_ways,
                include_relations=include_relations,
                query_timeout_s=timeout_s,
            )
            data = _overpass_fetch(q, timeout_s=timeout_s, retries=3)

            results["by_category"][category] = {
                "tag_key": tag_key,
                "tag_value": tag_value,
                "element_count": len(data.get("elements", [])),
                "data": data,
            }

            time.sleep(1.0)

        raw = results
        write_json(raw_path, raw)

    all_rows: list[dict[str, Any]] = []
    by_cat = raw.get("by_category", {})
    for category, payload in by_cat.items():
        data = payload.get("data") or {}
        elements = data.get("elements") or []
        all_rows.extend(_elements_to_rows(elements, category=category))

    df = pd.DataFrame(all_rows)
    if df.empty:
        raise RuntimeError("No OSM amenities were returned (rate limit or overly broad query).")

    df = df.dropna(subset=["osm_uid", "lon", "lat"]).drop_duplicates(subset=["osm_uid", "category"]).copy()

    amenities_gdf = gpd.GeoDataFrame(
        df.drop(columns=["lon", "lat"]),
        geometry=gpd.points_from_xy(df["lon"], df["lat"], crs="EPSG:4326"),
        crs="EPSG:4326",
    )

    amenities_gdf = drop_empty_geometries(amenities_gdf)
    amenities_gdf.to_file(curated_path, driver="GeoJSON")

    return amenities_gdf


if __name__ == "__main__":
    gdf_out = ingest_osm_amenities(force_fetch=False)
    print(f"Ingested OSM amenities: {len(gdf_out):,}")
    print(f"Wrote: {CFG.curated_dir / 'fact_amenities.geojson'}")
    print(gdf_out["category"].value_counts().to_dict())