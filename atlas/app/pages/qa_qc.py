from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from atlas.config import CFG

def _county_label(fips: str) -> str:
    name = getattr(CFG, "county_names", {}).get(fips)
    return f"{name or fips} ({fips})"


def _require_artifact(path: Path, *, label: str) -> None:
    if not path.exists():
        st.error(
            f"Missing required artifact: `{path}` ({label}).\n\n"
            "Run the pipeline first:\n"
            "```bash\n"
            ".venv/bin/python -m atlas.pipeline.refresh\n"
            "```"
        )
        st.stop()


@st.cache_data(show_spinner=False)
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@st.cache_data(show_spinner=False)
def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _load_geo(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS set.")
    if str(gdf.crs) != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def _assign_points_to_tracts_within(points_wgs: gpd.GeoDataFrame, tracts_wgs: gpd.GeoDataFrame) -> pd.Series:
    """
    Assign points to tracts via point-in-polygon (within).
    Uses sjoin if available, else a safe brute-force fallback.
    """
    try:
        joined = gpd.sjoin(
            points_wgs[["geometry"]],
            tracts_wgs[["tract_geoid", "geometry"]],
            how="left",
            predicate="within",
        )
        return joined["tract_geoid"]
    except (ImportError, ModuleNotFoundError, ValueError, TypeError):
        tract_geoms = list(tracts_wgs.geometry)
        tract_geoids = list(tracts_wgs["tract_geoid"])

        out: list[Any] = []
        for pt in points_wgs.geometry:
            assigned = pd.NA
            if pt is None or getattr(pt, "is_empty", False):
                out.append(assigned)
                continue

            for poly, geoid in zip(tract_geoms, tract_geoids):
                if pt.within(poly):
                    assigned = geoid
                    break
            out.append(assigned)

        return pd.Series(out, index=points_wgs.index, dtype="object")


def _render_outside_points_map(tracts_wgs: gpd.GeoDataFrame, outside_pts: gpd.GeoDataFrame, *, height: int = 520) -> None:
    import folium

    # center from tracts bounds
    minx, miny, maxx, maxy = tracts_wgs.total_bounds
    center_lat = (miny + maxy) / 2
    center_lon = (minx + maxx) / 2

    m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles="cartodbpositron")

    # tract outlines
    folium.GeoJson(
        tracts_wgs.__geo_interface__,
        name="Tracts",
        style_function=lambda _: {"fillOpacity": 0.0, "weight": 0.6},
    ).add_to(m)

    # points
    for _, row in outside_pts.iterrows():
        geom = row.geometry
        if geom is None or getattr(geom, "is_empty", False):
            continue

        cat = str(row.get("category", "unknown"))
        name = str(row.get("name", "") or "")
        uid = str(row.get("osm_uid", "") or "")

        popup = f"{cat}<br/>{name}<br/>{uid}"
        folium.CircleMarker(
            location=[geom.y, geom.x],
            radius=6,
            popup=popup,
            fill=True,
        ).add_to(m)

    folium.LayerControl(collapsed=True).add_to(m)
    components.html(m.get_root().render(), height=height)


def render() -> None:
    st.title("QA / QC")
    region_label = getattr(CFG, "region_label", "Atlas region")
    st.caption(f"Pipeline data quality checks and validation artifacts for {region_label}.")

    qa_json_path = CFG.curated_dir / "qa_report.json"
    qa_md_path = CFG.curated_dir / "qa_report.md"
    tracts_path = CFG.curated_dir / "dim_tracts.geojson"
    amenities_path = CFG.curated_dir / "fact_amenities.geojson"

    _require_artifact(qa_json_path, label="QA report (JSON)")
    _require_artifact(qa_md_path, label="QA report (Markdown)")

    report = _read_json(qa_json_path)

    # Pull key metrics safely
    tracts = report.get("tracts", {})
    amenities = report.get("amenities", {})
    cross = report.get("cross_checks", {})

    tr_rows = int(tracts.get("rows", 0))
    tr_invalid = int(tracts.get("invalid_geometry", 0))
    tr_null = int(tracts.get("null_geometry", 0))
    tr_empty = int(tracts.get("empty_geometry", 0))

    am_rows = int(amenities.get("rows", 0))
    am_invalid = int(amenities.get("invalid_geometry", 0))
    am_null = int(amenities.get("null_geometry", 0))
    am_empty = int(amenities.get("empty_geometry", 0))

    outside = cross.get("amenities_outside_tracts", None)
    outside = int(outside) if outside is not None else None

    # KPI cards
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tracts", f"{tr_rows:,}")
    c2.metric("Amenities", f"{am_rows:,}")
    c3.metric("Invalid tract geometries", f"{tr_invalid:,}")
    c4.metric("Amenities outside tracts", "n/a" if outside is None else f"{outside:,}")

    # Detail metrics
    with st.expander("Geometry checks", expanded=False):
        left, right = st.columns(2)
        with left:
            st.markdown("**Tracts (dim_tracts)**")
            st.write(
                {
                    "null_geometry": tr_null,
                    "empty_geometry": tr_empty,
                    "invalid_geometry": tr_invalid,
                    "crs": tracts.get("crs"),
                }
            )
        with right:
            st.markdown("**Amenities (fact_amenities)**")
            st.write(
                {
                    "null_geometry": am_null,
                    "empty_geometry": am_empty,
                    "invalid_geometry": am_invalid,
                    "crs": amenities.get("crs"),
                    "missing_name_pct": amenities.get("missing_name_pct"),
                    "duplicate_osm_uid_category": amenities.get("duplicate_osm_uid_category"),
                }
            )

    # Breakdown tables + chart
    county_counts = tracts.get("county_counts", {}) or {}
    category_counts = amenities.get("category_counts", {}) or {}

    b1, b2 = st.columns(2)
    with b1:
        st.subheader("Tracts by county")
        if county_counts:
            df_counties = (
                pd.DataFrame([{"county_fips": k, "tracts": v} for k, v in county_counts.items()])
                .assign(county=lambda d: d["county_fips"].astype(str).map(lambda x: _county_label(x)))
                .sort_values("tracts", ascending=False)
            )
            st.dataframe(df_counties[["county", "tracts"]], width="stretch")
        else:
            st.info("No county breakdown found in QA report.")

    with b2:
        st.subheader("Amenities by category")
        if category_counts:
            df_cats = (
                pd.DataFrame([{"category": k, "count": v} for k, v in category_counts.items()])
                .sort_values("count", ascending=False)
            )
            st.dataframe(df_cats, width="stretch")

            fig = px.bar(df_cats, x="category", y="count", title="OSM amenities (nodes) by category")
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No category breakdown found in QA report.")

    # Show outside-tract amenities (if any)
    if outside and outside > 0:
        st.subheader("Amenities outside tract polygons")
        st.caption(
            "These are POIs returned by the Overpass bounding box that do not fall within the tract polygons. "
            "This can happen near county boundaries or because the bbox includes some area outside the tri-county region."
        )

        _require_artifact(tracts_path, label="Curated tracts (dim_tracts.geojson)")
        _require_artifact(amenities_path, label="Curated amenities (fact_amenities.geojson)")

        tracts_gdf = _load_geo(tracts_path)
        amenities_gdf = _load_geo(amenities_path)

        # compute unassigned points
        tract_ids = _assign_points_to_tracts_within(
            points_wgs=amenities_gdf[["geometry"]].copy(),
            tracts_wgs=tracts_gdf[["tract_geoid", "geometry"]].copy(),
        )

        amen = amenities_gdf.copy()
        amen["tract_geoid"] = tract_ids
        outside_pts = amen[amen["tract_geoid"].isna()].copy()

        # Table
        show_cols = [c for c in ["category", "name", "osm_uid"] if c in outside_pts.columns]
        outside_tbl = outside_pts[show_cols].copy() if show_cols else pd.DataFrame()

        # add lat/lon for readability
        outside_tbl["lon"] = outside_pts.geometry.x
        outside_tbl["lat"] = outside_pts.geometry.y

        st.dataframe(outside_tbl, width="stretch")

        # Map
        _render_outside_points_map(tracts_gdf, outside_pts, height=540)

    # Show markdown report
    with st.expander("QA report (Markdown)", expanded=False):
        st.markdown(_read_text(qa_md_path))

    # Show raw JSON
    with st.expander("QA report (JSON)", expanded=False):
        st.json(report)


if __name__ == "__main__":
    render()