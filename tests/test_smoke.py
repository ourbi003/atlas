from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from atlas.config import CFG


def _artifact(path: Path) -> Path:
    """
    Smoke-test helper:
    - If artifacts are missing, default behavior is SKIP (dev-friendly).
    - If you want missing artifacts to FAIL (CI-friendly), set:
        ATLAS_REQUIRE_ARTIFACTS=1
    """
    require = os.getenv("ATLAS_REQUIRE_ARTIFACTS", "0") == "1"
    if not path.exists():
        msg = (
            f"Missing artifact: {path}\n\n"
            f"Current curated_dir: {CFG.curated_dir}\n\n"
            "Generate it by running (with the same ATLAS_CONFIG env, if you use one):\n"
            "  .venv/bin/python -m atlas.pipeline.refresh\n\n"
            "To make this a hard failure (recommended for CI), set:\n"
            "  ATLAS_REQUIRE_ARTIFACTS=1"
        )
        if require:
            pytest.fail(msg)
        pytest.skip(msg)
    return path


def test_curated_artifacts_exist() -> None:
    curated = CFG.curated_dir

    expected = [
        curated / "dim_tracts.geojson",
        curated / "fact_amenities.geojson",
        curated / "mart_access_wide.csv",
        curated / "mart_access_tracts.geojson",
        curated / "qa_report.json",
        curated / "model_report.json",
    ]
    for p in expected:
        _artifact(p)


def test_dim_tracts_geojson_loads_and_has_schema() -> None:
    gpd = pytest.importorskip("geopandas")

    path = _artifact(CFG.curated_dir / "dim_tracts.geojson")

    gdf = gpd.read_file(path)
    assert len(gdf) > 0, "dim_tracts.geojson is empty"
    assert gdf.crs is not None, "dim_tracts.geojson has no CRS"
    assert str(gdf.crs) == "EPSG:4326", f"Expected EPSG:4326, got {gdf.crs}"

    required = {"tract_geoid", "county_fips", "tract_name", "geometry"}
    missing = required - set(gdf.columns)
    assert not missing, f"dim_tracts.geojson missing columns: {sorted(missing)}"

    assert gdf["tract_geoid"].isna().sum() == 0, "tract_geoid contains nulls"
    assert gdf["tract_geoid"].nunique() == len(gdf), "tract_geoid is not unique"


def test_fact_amenities_geojson_loads_and_has_schema() -> None:
    gpd = pytest.importorskip("geopandas")

    path = _artifact(CFG.curated_dir / "fact_amenities.geojson")

    gdf = gpd.read_file(path)
    assert len(gdf) > 0, "fact_amenities.geojson is empty"
    assert gdf.crs is not None, "fact_amenities.geojson has no CRS"
    assert str(gdf.crs) == "EPSG:4326", f"Expected EPSG:4326, got {gdf.crs}"

    required = {"category", "osm_uid", "geometry"}
    missing = required - set(gdf.columns)
    assert not missing, f"fact_amenities.geojson missing columns: {sorted(missing)}"

    assert gdf["category"].isna().sum() == 0, "category contains nulls"
    assert gdf["osm_uid"].isna().sum() == 0, "osm_uid contains nulls"


def test_mart_access_wide_csv_loads_and_has_schema() -> None:
    pd = pytest.importorskip("pandas")

    path = _artifact(CFG.curated_dir / "mart_access_wide.csv")

    df = pd.read_csv(path)
    assert len(df) > 0, "mart_access_wide.csv is empty"

    required = {"tract_geoid", "county_fips", "coverage_score", "amenity_total", "area_km2"}
    missing = required - set(df.columns)
    assert not missing, f"mart_access_wide.csv missing columns: {sorted(missing)}"

    assert (df["amenity_total"].fillna(0) >= 0).all(), "amenity_total has negative values"
    assert (df["area_km2"].fillna(0) >= 0).all(), "area_km2 has negative values"

    has_cols = [c for c in df.columns if c.startswith("has_")]
    if has_cols:
        max_possible = len(has_cols)
        s = df["coverage_score"].fillna(0)
        assert (s >= 0).all(), "coverage_score has negative values"
        assert (s <= max_possible).all(), f"coverage_score exceeds max_possible={max_possible}"


def test_mart_access_tracts_geojson_loads_and_has_expected_columns() -> None:
    gpd = pytest.importorskip("geopandas")

    path = _artifact(CFG.curated_dir / "mart_access_tracts.geojson")

    gdf = gpd.read_file(path)
    assert len(gdf) > 0, "mart_access_tracts.geojson is empty"
    assert gdf.crs is not None, "mart_access_tracts.geojson has no CRS"
    assert str(gdf.crs) == "EPSG:4326", f"Expected EPSG:4326, got {gdf.crs}"

    required = {"tract_geoid", "county_fips", "tract_name", "geometry"}
    missing = required - set(gdf.columns)
    assert not missing, f"mart_access_tracts.geojson missing columns: {sorted(missing)}"

    required_metrics = {"coverage_score", "amenity_total", "amenities_per_km2_total", "area_km2"}
    missing_metrics = required_metrics - set(gdf.columns)
    assert not missing_metrics, f"mart_access_tracts.geojson missing metric columns: {sorted(missing_metrics)}"


def test_qa_and_model_reports_are_valid_json() -> None:
    qa_path = _artifact(CFG.curated_dir / "qa_report.json")
    model_path = _artifact(CFG.curated_dir / "model_report.json")

    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    model = json.loads(model_path.read_text(encoding="utf-8"))

    assert isinstance(qa, dict), "qa_report.json is not a JSON object"
    assert isinstance(model, dict), "model_report.json is not a JSON object"

    assert "generated_at_utc" in qa, "qa_report.json missing generated_at_utc"
    assert "generated_at_utc" in model, "model_report.json missing generated_at_utc"

    for key in ("inputs", "tracts", "amenities", "cross_checks"):
        assert key in qa and isinstance(qa[key], dict), f"qa_report.json missing {key} object"

    for section in ("tracts", "amenities"):
        for field in ("rows", "crs", "null_geometry", "empty_geometry", "invalid_geometry", "notes"):
            assert field in qa[section], f"qa_report.json missing {section}.{field}"

    cc = qa["cross_checks"]
    for field in ("amenities_outside_tracts", "tracts_with_zero_amenities_by_category", "notes"):
        assert field in cc, f"qa_report.json missing cross_checks.{field}"

    assert "stats" in model and isinstance(model["stats"], dict), "model_report.json missing stats"