from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from atlas.config import CFG

from atlas.app.ui_helpers import (
    norm_county_fips,
    county_name_map_from_df,
    merge_county_name_maps,
    format_county_option,
    require_artifact,
)


@st.cache_data(show_spinner=False)
def _load_wide_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


@st.cache_data(show_spinner=False)
def _load_tracts_geojson(path: Path) -> gpd.GeoDataFrame:
    return gpd.read_file(path)


def _render_folium_map(gdf_wgs: gpd.GeoDataFrame, *, value_col: str, height: int = 520) -> None:
    """Render a lightweight Folium choropleth preview without extra dependencies."""
    import folium  # local import keeps initial Streamlit reruns faster

    if gdf_wgs.empty:
        st.info("No features to map for the current filter.")
        return

    minx, miny, maxx, maxy = gdf_wgs.total_bounds
    center_lat = (miny + maxy) / 2
    center_lon = (minx + maxx) / 2

    m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles="cartodbpositron")

    data_df = gdf_wgs[["tract_geoid", value_col]].copy()
    data_df[value_col] = data_df[value_col].fillna(0)

    folium.Choropleth(
        geo_data=gdf_wgs.__geo_interface__,
        data=data_df,
        columns=["tract_geoid", value_col],
        key_on="feature.properties.tract_geoid",
        fill_opacity=0.7,
        line_opacity=0.2,
        legend_name=value_col,
        nan_fill_opacity=0.0,
    ).add_to(m)

    components.html(m.get_root().render(), height=height)


def render() -> None:
    st.title("Atlas")
    region_label = getattr(CFG, "region_label", "Atlas region")
    st.caption(f"{region_label} — TIGER/Line tracts + OpenStreetMap POI nodes, modeled into tract-level access metrics.")

    wide_csv = CFG.curated_dir / "mart_access_wide.csv"
    tracts_geojson = CFG.curated_dir / "mart_access_tracts.geojson"
    require_artifact(wide_csv, label="Modeled access mart (wide)")
    require_artifact(tracts_geojson, label="Modeled tracts GeoJSON")

    wide = _load_wide_csv(wide_csv)

    required_cols = {"tract_geoid", "county_fips", "coverage_score", "amenity_total", "area_km2"}
    missing = required_cols - set(wide.columns)
    if missing:
        st.error(f"`mart_access_wide.csv` is missing expected columns: {sorted(missing)}")
        st.stop()

    # Normalize county_fips ONCE (prevents int/float mismatches)
    wide = wide.copy()
    wide["county_fips"] = wide["county_fips"].map(norm_county_fips)

    # County label map: CFG + artifact (artifact wins if present)
    artifact_county_names = county_name_map_from_df(wide)
    county_label_map = merge_county_name_maps(
        cfg_names=getattr(CFG, "county_names", None),
        artifact_names=artifact_county_names,
    )

    # Sidebar filters
    st.sidebar.header("Filters")

    county_options = ["ALL"] + sorted(wide["county_fips"].dropna().unique().tolist())
    county_choice = st.sidebar.selectbox(
        "County",
        options=county_options,
        format_func=lambda x: format_county_option(x, county_label_map),
    )

    max_score = len(getattr(CFG, "osm_categories", {})) or 3
    cols_present = set(wide.columns)

    observed_cov_score = int(pd.to_numeric(wide["coverage_score"], errors="coerce").fillna(0).max()) if "coverage_score" in cols_present else 0
    cov_score_max = max(max_score, observed_cov_score)

    observed_buf_score = int(pd.to_numeric(wide["buffer_access_score"], errors="coerce").fillna(0).max()) if "buffer_access_score" in cols_present else 0
    buf_score_max = max(max_score, observed_buf_score)

    metric_options: list[tuple[str, str]] = []
    if "buffer_access_score" in cols_present:
        metric_options.append(("buffer_access_score", f"Buffer access score (0–{buf_score_max})"))
    if "buffer_amenity_total" in cols_present:
        metric_options.append(("buffer_amenity_total", "Buffer amenities (total)"))
    if "buffer_amenities_per_km2_total" in cols_present:
        metric_options.append(("buffer_amenities_per_km2_total", "Buffer amenities per km² (total)"))

    if "coverage_score" in cols_present:
        metric_options.append(("coverage_score", f"Coverage score (0–{cov_score_max})"))
    if "amenity_total" in cols_present:
        metric_options.append(("amenity_total", "Total amenities"))
    if "amenities_per_km2_total" in cols_present:
        metric_options.append(("amenities_per_km2_total", "Amenities per km² (total)"))

    if not metric_options:
        st.error("No supported metric columns found in `mart_access_wide.csv`.")
        st.stop()

    default_metric_candidates = [
        "buffer_amenities_per_km2_total",
        "buffer_access_score",
        "amenities_per_km2_total",
        "amenity_total",
        "coverage_score",
    ]
    default_metric = next((m for m in default_metric_candidates if any(k == m for k, _ in metric_options)), metric_options[0][0])
    default_index = next(i for i, (k, _) in enumerate(metric_options) if k == default_metric)

    metric_choice = st.sidebar.selectbox(
        "Map metric (preview)",
        options=metric_options,
        format_func=lambda t: t[1],
        index=default_index,
    )[0]

    show_map = st.sidebar.checkbox("Show choropleth preview map", value=True)

    # --- Optional comparability filter (UI only; does not change artifacts) ---
    hide_large_tracts = False
    max_area_km2_filter: float | None = None

    if "area_km2" in wide.columns:
        area_scope = wide if county_choice == "ALL" else wide[wide["county_fips"] == county_choice]
        area_series = pd.to_numeric(area_scope["area_km2"], errors="coerce").dropna()
        if not area_series.empty:
            p95_area = float(area_series.quantile(0.95))
            max_area_observed = float(area_series.max())

            hide_large_tracts = st.sidebar.checkbox("Hide very large tracts", value=False)
            if hide_large_tracts:
                slider_max = max(max_area_observed, 1.0)
                slider_default = min(max(p95_area, 0.0), slider_max)
                slider_step = max(slider_max / 200.0, 0.1)

                max_area_km2_filter = float(
                    st.sidebar.slider(
                        "Max tract area (km²)",
                        min_value=0.0,
                        max_value=slider_max,
                        value=slider_default,
                        step=slider_step,
                    )
                )
                st.sidebar.caption(
                    "Helps reduce distortion from unusually large tracts "
                    "(e.g., wetlands/rural tracts) when comparing tract-level metrics."
                )

    # Filter dataset
    df = wide if county_choice == "ALL" else wide[wide["county_fips"] == county_choice].copy()

    if hide_large_tracts and max_area_km2_filter is not None and "area_km2" in df.columns:
        df = df[pd.to_numeric(df["area_km2"], errors="coerce").fillna(float("inf")) <= max_area_km2_filter].copy()

    # KPIs (kept as-is; still centered on legacy metrics)
    total_tracts = int(len(df))
    tracts_covered = int((df["coverage_score"] >= 1).sum()) if "coverage_score" in df.columns else 0
    pct_covered = (tracts_covered / total_tracts * 100.0) if total_tracts else 0.0

    total_amenities = int(df["amenity_total"].fillna(0).sum()) if "amenity_total" in df.columns else 0
    mean_score = float(df["coverage_score"].mean()) if ("coverage_score" in df.columns and total_tracts) else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tracts", f"{total_tracts:,}")
    c2.metric("Tracts with ≥1 category", f"{tracts_covered:,}", f"{pct_covered:.1f}%")
    c3.metric("Total amenities (assigned)", f"{total_amenities:,}")
    c4.metric("Avg coverage score", f"{mean_score:.2f}")

    with st.expander("How to interpret coverage_score", expanded=False):
        st.markdown(
            f"""
**coverage_score (0–{max_score})** is a *category presence* proxy per tract.

- For each category (e.g., groceries, pharmacy, parks), the tract gets **1 point** if it contains **≥1** OpenStreetMap POI **node** for that category.
- The score is the **sum across categories**.

This is **not** a travel-time / “15-minute” measure yet. It does **not** account for distance, population, or POIs mapped as polygons/ways (ways/relations).
"""
        )

    if "buffer_access_score" in df.columns:
        with st.expander("How to interpret buffer_access_score (15-minute proxy)", expanded=False):
            buf_max = max(
                len(getattr(CFG, "osm_categories", {})) or 3,
                int(pd.to_numeric(df["buffer_access_score"], errors="coerce").fillna(0).max()),
            )
            buffer_meters = getattr(CFG, "buffer_meters", 800)
            st.markdown(
                f"""
**buffer_access_score (0–{buf_max})** is a *nearby access proxy* per tract.

- For each category (e.g., groceries, pharmacy, parks), the tract gets **1 point** if there is **≥1** POI within **{buffer_meters:,} m** of the tract centroid.
- The score is the **sum across categories**.

This is a better proxy than tract-inside counts for intuition, but it is still **not** a true travel-time / network-based measure.
"""
            )

    # Score distribution chart (kept as-is)
    st.subheader("Coverage distribution")
    score_counts = (
        df["coverage_score"]
        .fillna(0)
        .astype(int)
        .value_counts()
        .sort_index()
        .rename_axis("coverage_score")
        .reset_index(name="tract_count")
    )

    fig = px.bar(
        score_counts,
        x="coverage_score",
        y="tract_count",
        title="Tracts by coverage score",
        labels={"coverage_score": f"Coverage score (0–{max_score})", "tract_count": "Number of tracts"},
    )
    st.plotly_chart(fig, width="stretch")

    with st.expander("Assumptions & MVP limitations", expanded=False):
        st.markdown(
            "- **Access proxy:** Current MVP uses tract-level aggregation of POI points.\n"
            "- **OSM scope:** Ingestion is **nodes-only** (ways/relations are excluded for reliability on large areas).\n"
            "- **Zeros are informative:** Many tracts may show 0 amenities due to OSM tagging coverage and nodes-only constraints.\n"
            "- **Next upgrade:** add polygon/way POIs via tiling queries, plus buffer-based coverage metrics."
        )

    if show_map:
        st.subheader("Map preview")
        tracts = _load_tracts_geojson(tracts_geojson)
        if "county_fips" in tracts.columns:
            tracts = tracts.copy()
            tracts["county_fips"] = tracts["county_fips"].map(norm_county_fips)

        if tracts.crs is None or str(tracts.crs) != "EPSG:4326":
            tracts = tracts.to_crs("EPSG:4326")

        if county_choice != "ALL" and "county_fips" in tracts.columns:
            tracts = tracts[tracts["county_fips"] == county_choice].copy()

        if hide_large_tracts and max_area_km2_filter is not None and "area_km2" in tracts.columns:
            tracts = tracts[pd.to_numeric(tracts["area_km2"], errors="coerce").fillna(float("inf")) <= max_area_km2_filter].copy()

        if metric_choice not in tracts.columns:
            st.warning(f"Metric `{metric_choice}` not found in `mart_access_tracts.geojson`.")
        else:
            _render_folium_map(tracts, value_col=metric_choice, height=560)


if __name__ == "__main__":
    render()
