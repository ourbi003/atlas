from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import geopandas as gpd
import pandas as pd

from atlas.config import CFG
from atlas.utils.geo import require_crs
from atlas.utils.io import ensure_dir, write_json


@dataclass(frozen=True)
class LayerQA:
    rows: int
    crs: str | None
    null_geometry: int
    empty_geometry: int
    invalid_geometry: int
    notes: list[str]


def _count_invalid_geometries(gdf: gpd.GeoDataFrame) -> int:
    """
    Count invalid geometries, excluding null/empty.

    GeoPandas uses Shapely under the hood for validity. We keep this as a report
    metric (we do not auto-repair in QA).
    """
    geom = gdf.geometry
    not_null = geom.notna()
    not_empty = not_null & ~geom.is_empty

    # is_valid may error on some edge cases; guard by applying only where usable
    valid = pd.Series(False, index=gdf.index)
    if not_empty.any():
        valid.loc[not_empty] = geom.loc[not_empty].is_valid

    invalid = not_empty & ~valid
    return int(invalid.sum())


def _qa_layer(gdf: gpd.GeoDataFrame, *, layer_name: str) -> LayerQA:
    notes: list[str] = []

    crs_str = str(gdf.crs) if gdf.crs is not None else None
    if gdf.crs is None:
        notes.append(f"{layer_name}: CRS is None (set CRS before spatial ops).")

    null_geom = int(gdf.geometry.isna().sum())
    empty_geom = int((gdf.geometry.notna() & gdf.geometry.is_empty).sum())
    invalid_geom = _count_invalid_geometries(gdf)

    return LayerQA(
        rows=int(len(gdf)),
        crs=crs_str,
        null_geometry=null_geom,
        empty_geometry=empty_geom,
        invalid_geometry=invalid_geom,
        notes=notes,
    )


def _assign_points_to_tracts(points: gpd.GeoDataFrame, tracts: gpd.GeoDataFrame) -> pd.Series:
    """
    Return a Series tract_geoid per point (or <NA> if not assigned).

    Prefer GeoPandas sjoin (fast, clean). If unavailable due to missing spatial
    index support, fall back to a safe but slower approach.
    """
    # Ensure same CRS for spatial predicate
    require_crs(points, name="amenities")
    require_crs(tracts, name="tracts")

    if str(points.crs) != str(tracts.crs):
        points = points.to_crs(tracts.crs)

    # Attempt spatial join
    try:
        joined = gpd.sjoin(
            points[["geometry"]],
            tracts[["tract_geoid", "geometry"]],
            how="left",
            predicate="within",
        )
        return joined["tract_geoid"]
    except (ImportError, ModuleNotFoundError, ValueError, TypeError):
        # Fallback: brute-force per point against all tracts (OK for MVP size)
        # 837 points * 1497 tracts ≈ 1.25M "within" checks worst case.
        geoid_out: list[Any] = []
        tract_geoms = list(tracts.geometry)
        tract_geoids = list(tracts["tract_geoid"])

        for pt in points.geometry:
            assigned = pd.NA
            # quick skip if point is null/empty (should be rare)
            if pt is None or getattr(pt, "is_empty", False):
                geoid_out.append(assigned)
                continue

            for poly, geoid in zip(tract_geoms, tract_geoids):
                if pt.within(poly):
                    assigned = geoid
                    break
            geoid_out.append(assigned)

        return pd.Series(geoid_out, index=points.index, dtype="object")


def run_qa() -> dict[str, Any]:
    """
    Generate QA/QC report artifacts for Atlas curated layers.

    Writes:
    - data/curated/qa_report.json
    - data/curated/qa_report.md
    """
    ensure_dir(CFG.curated_dir)

    dim_tracts_path = CFG.curated_dir / "dim_tracts.geojson"
    amenities_path = CFG.curated_dir / "fact_amenities.geojson"

    if not dim_tracts_path.exists():
        raise FileNotFoundError(f"Missing {dim_tracts_path}. Run ingest_tiger.py first.")
    if not amenities_path.exists():
        raise FileNotFoundError(f"Missing {amenities_path}. Run ingest_osm.py first.")

    tracts = gpd.read_file(dim_tracts_path)
    amenities = gpd.read_file(amenities_path)

    # Basic QA per layer
    tracts_qa = _qa_layer(tracts, layer_name="dim_tracts")
    amenities_qa = _qa_layer(amenities, layer_name="fact_amenities")

    # Layer-specific checks
    counties = {}
    if "county_fips" in tracts.columns:
        counties = tracts["county_fips"].value_counts(dropna=False).to_dict()

    categories = {}
    missing_name_pct = None
    duplicate_uid_category = None

    if "category" in amenities.columns:
        categories = amenities["category"].value_counts(dropna=False).to_dict()

    if "name" in amenities.columns:
        name_series = amenities["name"]
        missing = name_series.isna() | (name_series.astype("string").str.strip() == "")
        missing_name_pct = float(missing.mean() * 100.0)

    if {"osm_uid", "category"}.issubset(set(amenities.columns)):
        duplicate_uid_category = int(amenities.duplicated(subset=["osm_uid", "category"]).sum())

    # Cross-checks: points within tracts + per-tract zero counts
    cross_notes: list[str] = []
    amenities_outside_tracts = None
    zero_by_category: dict[str, int] = {}

    # Ensure tract_geoid exists (expected schema from ingest_tiger)
    if "tract_geoid" not in tracts.columns:
        cross_notes.append("tract_geoid missing from dim_tracts; cannot run cross checks.")
    else:
        # Use WGS84 for reporting consistency
        require_crs(tracts, name="dim_tracts")
        require_crs(amenities, name="fact_amenities")

        tracts_wgs = tracts.to_crs("EPSG:4326") if str(tracts.crs) != "EPSG:4326" else tracts
        amenities_wgs = amenities.to_crs("EPSG:4326") if str(amenities.crs) != "EPSG:4326" else amenities

        tract_for_points = tracts_wgs[["tract_geoid", "geometry"]].copy()
        pts_for_join = amenities_wgs[["geometry"]].copy()

        tract_geoid_per_point = _assign_points_to_tracts(pts_for_join, tract_for_points)
        amenities_outside_tracts = int(tract_geoid_per_point.isna().sum())

        # Compute tracts with zero amenities per category (if category column exists)
        if "category" in amenities_wgs.columns:
            tmp = amenities_wgs.copy()
            tmp["tract_geoid"] = tract_geoid_per_point

            # drop outside points for per-tract counts
            tmp = tmp.dropna(subset=["tract_geoid"])

            # counts per tract/category
            counts = (
                tmp.groupby(["tract_geoid", "category"])
                .size()
                .rename("amenity_count")
                .reset_index()
            )

            all_tracts = set(tracts_wgs["tract_geoid"].tolist())
            for cat in sorted(amenities_wgs["category"].dropna().unique().tolist()):
                tracts_with_cat = set(counts.loc[counts["category"] == cat, "tract_geoid"].tolist())
                zero_by_category[cat] = int(len(all_tracts - tracts_with_cat))
        else:
            cross_notes.append("category missing from fact_amenities; cannot compute zero-by-category.")

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "dim_tracts": str(dim_tracts_path),
            "fact_amenities": str(amenities_path),
        },
        "tracts": {
            **asdict(tracts_qa),
            "county_counts": counties,
        },
        "amenities": {
            **asdict(amenities_qa),
            "category_counts": categories,
            "missing_name_pct": missing_name_pct,
            "duplicate_osm_uid_category": duplicate_uid_category,
        },
        "cross_checks": {
            "amenities_outside_tracts": amenities_outside_tracts,
            "tracts_with_zero_amenities_by_category": zero_by_category,
            "notes": cross_notes,
        },
    }

    # Write JSON report
    json_path = CFG.curated_dir / "qa_report.json"
    write_json(json_path, report)

    # Write a simple Markdown summary for GitHub readability
    md_path = CFG.curated_dir / "qa_report.md"
    md_lines = [
        "# Atlas QA Report",
        "",
        f"- Generated (UTC): `{report['generated_at_utc']}`",
        "",
        "## Tracts (dim_tracts)",
        f"- Rows: **{tracts_qa.rows:,}**",
        f"- CRS: `{tracts_qa.crs}`",
        f"- Null geometries: **{tracts_qa.null_geometry}**",
        f"- Empty geometries: **{tracts_qa.empty_geometry}**",
        f"- Invalid geometries: **{tracts_qa.invalid_geometry}**",
        f"- County counts: `{counties}`",
        "",
        "## Amenities (fact_amenities)",
        f"- Rows: **{amenities_qa.rows:,}**",
        f"- CRS: `{amenities_qa.crs}`",
        f"- Null geometries: **{amenities_qa.null_geometry}**",
        f"- Empty geometries: **{amenities_qa.empty_geometry}**",
        f"- Invalid geometries: **{amenities_qa.invalid_geometry}**",
        f"- Category counts: `{categories}`",
    ]
    if missing_name_pct is not None:
        md_lines.append(f"- Missing/blank `name` (%): **{missing_name_pct:.2f}%**")
    if duplicate_uid_category is not None:
        md_lines.append(f"- Duplicate (`osm_uid`, `category`) rows: **{duplicate_uid_category}**")

    md_lines += [
        "",
        "## Cross-checks",
        f"- Amenities outside tract polygons: **{amenities_outside_tracts if amenities_outside_tracts is not None else 'n/a'}**",
        f"- Tracts with zero amenities by category: `{zero_by_category}`",
    ]
    if cross_notes:
        md_lines += ["", "### Notes"] + [f"- {n}" for n in cross_notes]

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return report


if __name__ == "__main__":
    out = run_qa()
    print(f"Wrote: {CFG.curated_dir / 'qa_report.json'}")
    print(f"Wrote: {CFG.curated_dir / 'qa_report.md'}")
    print("Amenities outside tracts:", out["cross_checks"]["amenities_outside_tracts"])
    print("Zero-amenity tracts by category:", out["cross_checks"]["tracts_with_zero_amenities_by_category"])