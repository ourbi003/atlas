from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import geopandas as gpd

from atlas.config import CFG
from atlas.utils.geo import drop_empty_geometries, to_crs_safe
from atlas.utils.io import download_file, ensure_dir


def tiger_tract_zip_url(*, year: int, state_fips: str) -> str:
    """
    Build the TIGER/Line URL for state-level tract boundaries.

    Example pattern:
    https://www2.census.gov/geo/tiger/TIGER2023/TRACT/tl_2023_12_tract.zip
    """
    return f"{CFG.tiger_base_url}/TIGER{year}/TRACT/tl_{year}_{state_fips}_tract.zip"

def tiger_county_zip_url(*, year: int) -> str:
    """
    Build the TIGER/Line URL for the national county boundaries file.

    Example pattern:
    https://www2.census.gov/geo/tiger/TIGER2023/COUNTY/tl_2023_us_county.zip
    """
    return f"{CFG.tiger_base_url}/TIGER{year}/COUNTY/tl_{year}_us_county.zip"


def _find_county_shapefile(extract_dir: Path, *, year: int) -> Path:
    """
    Locate the expected county shapefile inside the extracted TIGER directory.
    Falls back to the first .shp it finds if the expected name isn't present.
    """
    expected = extract_dir / f"tl_{year}_us_county.shp"
    if expected.exists():
        return expected

    shp_files = sorted(extract_dir.glob("*.shp"))
    if not shp_files:
        raise FileNotFoundError(f"No .shp found in extracted directory: {extract_dir}")
    return shp_files[0]

def _extract_zip(zip_path: Path, extract_dir: Path, *, overwrite: bool = False) -> None:
    """
    Extract a ZIP file to a directory.
    If overwrite=True, delete the extract_dir first.
    """
    if extract_dir.exists() and overwrite:
        shutil.rmtree(extract_dir)

    if extract_dir.exists():
        return

    ensure_dir(extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)


def _find_shapefile(extract_dir: Path, *, year: int, state_fips: str) -> Path:
    """
    Locate the expected tract shapefile inside the extracted TIGER directory.
    Falls back to the first .shp it finds if the expected name isn't present.
    """
    expected = extract_dir / f"tl_{year}_{state_fips}_tract.shp"
    if expected.exists():
        return expected

    shp_files = sorted(extract_dir.glob("*.shp"))
    if not shp_files:
        raise FileNotFoundError(f"No .shp found in extracted directory: {extract_dir}")
    return shp_files[0]


def ingest_tracts(*, force_download: bool = False, force_extract: bool = False) -> gpd.GeoDataFrame:
    """
    Ingest Census TIGER/Line tract boundaries for the Tri-County region.

    Outputs:
    - data/curated/dim_tracts.geojson (EPSG:4326)

    Returns:
    - GeoDataFrame of tracts (EPSG:4326)
    """
    ensure_dir(CFG.raw_dir)
    ensure_dir(CFG.curated_dir)

    zip_name = f"tl_{CFG.tiger_year}_{CFG.state_fips}_tract.zip"
    zip_path = CFG.raw_dir / zip_name

    url = tiger_tract_zip_url(year=CFG.tiger_year, state_fips=CFG.state_fips)
    download_file(url, zip_path, overwrite=force_download, timeout_s=60)

    extract_dir = CFG.raw_dir / f"tl_{CFG.tiger_year}_{CFG.state_fips}_tract"
    _extract_zip(zip_path, extract_dir, overwrite=force_extract)

    shp_path = _find_shapefile(extract_dir, year=CFG.tiger_year, state_fips=CFG.state_fips)
    gdf = gpd.read_file(shp_path)

    # Filter to configured county scope (normalize FIPS defensively)
    for col in ("STATEFP", "COUNTYFP"):
        if col not in gdf.columns:
            raise KeyError(f"Expected column '{col}' not found in TIGER tract dataset.")

    gdf = gdf.copy()
    gdf["STATEFP"] = gdf["STATEFP"].astype(str).str.zfill(2)
    gdf["COUNTYFP"] = gdf["COUNTYFP"].astype(str).str.zfill(3)

    gdf = gdf[
        (gdf["STATEFP"] == str(CFG.state_fips).zfill(2))
        & (gdf["COUNTYFP"].isin(tuple(str(c).zfill(3) for c in CFG.county_fips)))
        ].copy()

    # Enrich tracts with county_name from TIGER county layer (data-driven, region-agnostic UI labels)
    county_zip_name = f"tl_{CFG.tiger_year}_us_county.zip"
    county_zip_path = CFG.raw_dir / county_zip_name

    county_url = tiger_county_zip_url(year=CFG.tiger_year)
    download_file(county_url, county_zip_path, overwrite=force_download, timeout_s=120)

    county_extract_dir = CFG.raw_dir / f"tl_{CFG.tiger_year}_us_county"
    _extract_zip(county_zip_path, county_extract_dir, overwrite=force_extract)

    county_shp_path = _find_county_shapefile(county_extract_dir, year=CFG.tiger_year)
    counties = gpd.read_file(county_shp_path)

    for col in ("STATEFP", "COUNTYFP"):
        if col not in counties.columns:
            raise KeyError(f"Expected column '{col}' not found in TIGER county dataset.")

    counties = counties.copy()
    counties["STATEFP"] = counties["STATEFP"].astype(str).str.zfill(2)
    counties["COUNTYFP"] = counties["COUNTYFP"].astype(str).str.zfill(3)

    # Prefer NAME (e.g., 'Miami-Dade'); fallback to NAMELSAD if needed
    county_name_src = "NAME" if "NAME" in counties.columns else ("NAMELSAD" if "NAMELSAD" in counties.columns else None)
    if county_name_src is None:
        raise KeyError("TIGER county dataset missing both 'NAME' and 'NAMELSAD'.")

    county_lookup = (
        counties.loc[
            (counties["STATEFP"] == str(CFG.state_fips).zfill(2))
            & (counties["COUNTYFP"].isin(tuple(str(c).zfill(3) for c in CFG.county_fips))),
            ["STATEFP", "COUNTYFP", county_name_src],
        ]
        .drop_duplicates(subset=["STATEFP", "COUNTYFP"])
        .rename(columns={county_name_src: "county_name"})
    )

    gdf = gdf.merge(
        county_lookup,
        on=["STATEFP", "COUNTYFP"],
        how="left",
        validate="m:1",
    )

    if "county_name" not in gdf.columns or gdf["county_name"].isna().any():
        missing_names = sorted(gdf.loc[gdf["county_name"].isna(), "COUNTYFP"].astype(str).unique().tolist())
        raise RuntimeError(f"Failed to resolve county_name for COUNTYFP values: {missing_names}")

    # Keep only the columns we need for the dim table
    needed = ["GEOID", "NAME", "COUNTYFP", "county_name", "geometry"]
    missing = set(needed) - set(gdf.columns)
    if missing:
        raise KeyError(f"Missing expected TIGER columns: {sorted(missing)}")

    gdf = gdf[needed].rename(
        columns={
            "GEOID": "tract_geoid",
            "NAME": "tract_name",
            "COUNTYFP": "county_fips",
        }
    )

    # Drop null/empty geometries (keep invalid geometries for QA/QC to report)
    gdf = drop_empty_geometries(gdf)

    # Export in EPSG:4326 for web mapping (Folium/GeoJSON)
    gdf = to_crs_safe(gdf, "EPSG:4326", name="tracts_dim")

    out_path = CFG.curated_dir / "dim_tracts.geojson"
    gdf.to_file(out_path, driver="GeoJSON")

    return gdf


if __name__ == "__main__":
    tracts = ingest_tracts()
    print(f"Ingested tracts: {len(tracts):,}")
    print(f"Wrote: {CFG.curated_dir / 'dim_tracts.geojson'}")