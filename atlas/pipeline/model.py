from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import math

import geopandas as gpd
import pandas as pd

from atlas.config import CFG
from atlas.utils.geo import require_crs
from atlas.utils.io import ensure_dir, write_json


@dataclass(frozen=True)
class ModelOutputs:
    long_csv: Path
    wide_csv: Path
    tracts_geojson: Path
    report_json: Path


def _norm_county_fips_series(s: pd.Series) -> pd.Series:
    """
    Normalize county FIPS values to 3-digit strings.
    Handles ints, strings, and float-like strings (e.g., '86.0').
    """
    out = s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    return out.str.zfill(3)


def _assign_points_to_tracts_within(
    points_wgs: gpd.GeoDataFrame,
    tracts_wgs: gpd.GeoDataFrame,
) -> pd.Series:
    """
    Assign each point to a tract using a point-in-polygon test (within).

    Returns a Series aligned to points_wgs.index with tract_geoid (or <NA> if unassigned).

    Uses GeoPandas sjoin when available; falls back to a safe brute-force loop if spatial
    index support is missing.
    """
    require_crs(points_wgs, name="amenities")
    require_crs(tracts_wgs, name="tracts")

    if str(points_wgs.crs) != str(tracts_wgs.crs):
        points_wgs = points_wgs.to_crs(tracts_wgs.crs)

    try:
        joined = gpd.sjoin(
            points_wgs[["geometry"]],
            tracts_wgs[["tract_geoid", "geometry"]],
            how="left",
            predicate="within",
        )
        return joined["tract_geoid"]
    except (ImportError, ModuleNotFoundError, ValueError, TypeError):
        # Fallback: brute-force checks (OK for MVP sizes)
        tract_geoms = list(tracts_wgs.geometry)
        tract_geoids = list(tracts_wgs["tract_geoid"])

        assigned_geoids: list[Any] = []
        for pt in points_wgs.geometry:
            assigned = pd.NA
            if pt is None or getattr(pt, "is_empty", False):
                assigned_geoids.append(assigned)
                continue

            for poly, geoid in zip(tract_geoms, tract_geoids):
                if pt.within(poly):
                    assigned = geoid
                    break

            assigned_geoids.append(assigned)

        return pd.Series(assigned_geoids, index=points_wgs.index, dtype="object")


def build_access_mart(*, drop_unassigned_points: bool = True) -> ModelOutputs:
    """
    Build modeled access metrics (MVP):
    - amenity counts per tract per category
    - densities per km^2 (using tract area)
    - simple coverage_score = number of categories present (0..N)

    Also computes centroid-buffer ("15-minute proxy") metrics:
    - buffer_access_score, buffer_amenity_total, buffer_amenities_per_km2_total, etc.

    Inputs (curated):
    - data/curated/.../dim_tracts.geojson
    - data/curated/.../fact_amenities.geojson

    Outputs (modeled):
    - data/curated/.../mart_access_long.csv
    - data/curated/.../mart_access_wide.csv
    - data/curated/.../mart_access_tracts.geojson
    - data/curated/.../model_report.json
    """
    ensure_dir(CFG.curated_dir)

    tracts_path = CFG.curated_dir / "dim_tracts.geojson"
    amenities_path = CFG.curated_dir / "fact_amenities.geojson"

    if not tracts_path.exists():
        raise FileNotFoundError(f"Missing {tracts_path}. Run ingest_tiger.py first.")
    if not amenities_path.exists():
        raise FileNotFoundError(f"Missing {amenities_path}. Run ingest_osm.py first.")

    tracts = gpd.read_file(tracts_path)
    amenities = gpd.read_file(amenities_path)

    # Basic schema expectations
    required_tract_cols = {"tract_geoid", "county_fips", "tract_name", "geometry"}
    missing_tract = required_tract_cols - set(tracts.columns)
    if missing_tract:
        raise KeyError(f"dim_tracts missing expected columns: {sorted(missing_tract)}")

    required_amen_cols = {"category", "osm_uid", "geometry"}
    missing_amen = required_amen_cols - set(amenities.columns)
    if missing_amen:
        raise KeyError(f"fact_amenities missing expected columns: {sorted(missing_amen)}")

    require_crs(tracts, name="dim_tracts")
    require_crs(amenities, name="fact_amenities")

    # Ensure WGS84 for web mapping + stable joins
    tracts_wgs = tracts.to_crs("EPSG:4326") if str(tracts.crs) != "EPSG:4326" else tracts
    amenities_wgs = amenities.to_crs("EPSG:4326") if str(amenities.crs) != "EPSG:4326" else amenities

    # Normalize county_fips early so all downstream outputs preserve zero-padded values (e.g., "086")
    if "county_fips" in tracts_wgs.columns:
        tracts_wgs = tracts_wgs.copy()
        tracts_wgs["county_fips"] = _norm_county_fips_series(tracts_wgs["county_fips"])

    # Compute tract area in km^2 using projected CRS
    tracts_proj = tracts_wgs.to_crs(CFG.projected_crs)
    tracts_proj["area_km2"] = tracts_proj.geometry.area / 1_000_000.0

    # Categories + tract dimension table (include county_name if present)
    categories = sorted(list(CFG.osm_categories.keys()))
    tract_dim_cols = ["tract_geoid", "county_fips"]
    if "county_name" in tracts_proj.columns:
        tract_dim_cols.append("county_name")
    tract_dim_cols.extend(["tract_name", "area_km2"])

    all_tracts = tracts_proj[tract_dim_cols].copy()
    tract_geoids = all_tracts["tract_geoid"].tolist()

    # Build full tract x category grid ONCE (used for both legacy and buffer metrics)
    full_index = pd.MultiIndex.from_product(
        [tract_geoids, categories],
        names=["tract_geoid", "category"],
    )

    def _fill_full_grid(counts_df: pd.DataFrame) -> pd.DataFrame:
        """Ensure a complete tract×category grid with missing values filled as 0."""
        return (
            counts_df.set_index(["tract_geoid", "category"])
            .reindex(full_index, fill_value=0)
            .reset_index()
        )

    def _pivot_counts(counts_df_full: pd.DataFrame, *, prefix: str) -> pd.DataFrame:
        """Pivot amenity_count into wide columns, renaming category columns with a prefix."""
        wide = (
            counts_df_full.pivot_table(
                index="tract_geoid",
                columns="category",
                values="amenity_count",
                aggfunc="sum",
                fill_value=0,
            )
            .reset_index()
        )
        return wide.rename(columns={cat: f"{prefix}{c}" for cat in categories})

    # -------------------------
    # Legacy: point-in-polygon (within tract)
    # -------------------------
    tract_geoid_series = _assign_points_to_tracts_within(
        points_wgs=amenities_wgs[["geometry"]].copy(),
        tracts_wgs=tracts_wgs[["tract_geoid", "geometry"]].copy(),
    )

    amenities_with_tract = amenities_wgs.copy()
    amenities_with_tract["tract_geoid"] = tract_geoid_series

    total_points = int(len(amenities_with_tract))
    unassigned_points = int(amenities_with_tract["tract_geoid"].isna().sum())

    if drop_unassigned_points:
        amenities_in_scope = amenities_with_tract.dropna(subset=["tract_geoid"]).copy()
    else:
        amenities_in_scope = amenities_with_tract.copy()

    counts_long = (
        amenities_in_scope.groupby(["tract_geoid", "category"])
        .size()
        .rename("amenity_count")
        .reset_index()
    )

    counts_long_full = _fill_full_grid(counts_long)

    # Long output: add tract attributes (area + names) onto long table
    long_df = counts_long_full.merge(all_tracts, on="tract_geoid", how="left")
    long_df["amenities_per_km2"] = long_df["amenity_count"] / long_df["area_km2"].replace({0: pd.NA})
    long_df["has_amenity"] = long_df["amenity_count"] > 0

    # Wide output (legacy)
    wide_counts = _pivot_counts(counts_long_full, prefix="count_")

    for c in categories:
        col = f"count_{c}"
        wide_counts[f"has_{c}"] = wide_counts[col] > 0

    has_cols = [f"has_{c}" for c in categories]
    wide_counts["coverage_score"] = wide_counts[has_cols].sum(axis=1).astype(int)

    count_cols = [f"count_{c}" for c in categories]
    wide_counts["amenity_total"] = wide_counts[count_cols].sum(axis=1).astype(int)

    wide_df = all_tracts.merge(wide_counts, on="tract_geoid", how="left")
    wide_df["amenities_per_km2_total"] = wide_df["amenity_total"] / wide_df["area_km2"].replace({0: pd.NA})

    # -------------------------
    # Buffer-based access proxy (within CFG.buffer_meters of tract centroid)
    # -------------------------
    amenities_proj = amenities_wgs.to_crs(CFG.projected_crs)

    buffers = tracts_proj[["tract_geoid", "geometry"]].copy()
    buffers["geometry"] = tracts_proj.geometry.centroid.buffer(CFG.buffer_meters)

    try:
        joined_buf = gpd.sjoin(
            amenities_proj[["category", "geometry"]],
            buffers[["tract_geoid", "geometry"]],
            how="inner",
            predicate="within",
        )
        counts_buf = (
            joined_buf.groupby(["tract_geoid", "category"])
            .size()
            .rename("amenity_count")
            .reset_index()
        )
    except (ImportError, ModuleNotFoundError, ValueError, TypeError):
        tract_geoid_buf = _assign_points_to_tracts_within(
            points_wgs=amenities_proj[["geometry"]].copy(),
            tracts_wgs=buffers[["tract_geoid", "geometry"]].copy(),
        )
        tmp = amenities_proj.copy()
        tmp["tract_geoid"] = tract_geoid_buf
        tmp = tmp.dropna(subset=["tract_geoid"])
        counts_buf = (
            tmp.groupby(["tract_geoid", "category"])
            .size()
            .rename("amenity_count")
            .reset_index()
        )

    counts_buf_full = _fill_full_grid(counts_buf)
    buf_wide = _pivot_counts(counts_buf_full, prefix="buffer_count_")

    for c in categories:
        col = f"buffer_count_{c}"
        buf_wide[f"buffer_has_{c}"] = buf_wide[col] > 0

    buf_has_cols = [f"buffer_has_{c}" for c in categories]
    buf_count_cols = [f"buffer_count_{c}" for c in categories]

    buf_wide["buffer_access_score"] = buf_wide[buf_has_cols].sum(axis=1).astype(int)
    buf_wide["buffer_amenity_total"] = buf_wide[buf_count_cols].sum(axis=1).astype(int)

    buffer_area_km2 = math.pi * (CFG.buffer_meters ** 2) / 1_000_000.0
    buf_wide["buffer_area_km2"] = buffer_area_km2
    buf_wide["buffer_amenities_per_km2_total"] = buf_wide["buffer_amenity_total"] / buffer_area_km2

    # Merge buffer metrics into the main wide_df
    wide_df = wide_df.merge(buf_wide, on="tract_geoid", how="left")

    # -------------------------
    # Geo outputs
    # -------------------------
    wide_metrics = wide_df.drop(columns=["county_fips", "county_name", "tract_name"], errors="ignore")

    tracts_out = tracts_wgs.merge(
        wide_metrics,
        on="tract_geoid",
        how="left",
        validate="1:1",
    )

    # Output paths
    long_csv = CFG.curated_dir / "mart_access_long.csv"
    wide_csv = CFG.curated_dir / "mart_access_wide.csv"
    tracts_geojson = CFG.curated_dir / "mart_access_tracts.geojson"
    report_json = CFG.curated_dir / "model_report.json"

    # Write outputs
    long_df.to_csv(long_csv, index=False)
    wide_df.to_csv(wide_csv, index=False)
    tracts_out.to_file(tracts_geojson, driver="GeoJSON")

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "dim_tracts": str(tracts_path),
            "fact_amenities": str(amenities_path),
        },
        "config": {
            "projected_crs": CFG.projected_crs,
            "categories": categories,
            "buffer_meters": CFG.buffer_meters,
        },
        "stats": {
            "tract_rows": int(len(tracts_wgs)),
            "amenity_rows_total": total_points,
            "amenity_rows_unassigned": unassigned_points,
            "drop_unassigned_points": bool(drop_unassigned_points),
            "amenity_rows_used": int(len(amenities_in_scope)),
            "buffer_area_km2": buffer_area_km2,
        },
        "outputs": {
            "mart_access_long_csv": str(long_csv),
            "mart_access_wide_csv": str(wide_csv),
            "mart_access_tracts_geojson": str(tracts_geojson),
        },
    }
    write_json(report_json, report)

    return ModelOutputs(
        long_csv=long_csv,
        wide_csv=wide_csv,
        tracts_geojson=tracts_geojson,
        report_json=report_json,
    )


if __name__ == "__main__":
    outputs = build_access_mart(drop_unassigned_points=True)
    print("Wrote:", outputs.long_csv)
    print("Wrote:", outputs.wide_csv)
    print("Wrote:", outputs.tracts_geojson)
    print("Wrote:", outputs.report_json)
