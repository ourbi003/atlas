from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from atlas.config import CFG

def _norm_county_fips(v: object) -> str:
    """
    Normalize county FIPS to 3-digit string for stable UI labels and filtering.
    Handles values like 86, '86', '086', and '86.0'.
    """
    if pd.isna(v):
        return ""

    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]

    return s.zfill(3) if s.isdigit() else s

def _county_name_map_from_df(df: pd.DataFrame) -> dict[str, str]:
    """
    Build a county_fips -> county_name map from an artifact dataframe if available.
    """
    if "county_fips" not in df.columns or "county_name" not in df.columns:
        return {}

    tmp = df[["county_fips", "county_name"]].copy()
    tmp["county_fips"] = tmp["county_fips"].map(_norm_county_fips)
    tmp["county_name"] = tmp["county_name"].astype(str).str.strip()

    tmp = tmp[
        (tmp["county_fips"] != "")
        & (tmp["county_name"] != "")
        & (tmp["county_name"].str.lower() != "nan")
    ].drop_duplicates(subset=["county_fips"])

    return dict(zip(tmp["county_fips"], tmp["county_name"]))

def _county_label(fips: str) -> str:
    fips3 = _norm_county_fips(fips)
    raw_names = getattr(CFG, "county_names", {}) or {}
    county_names = {_norm_county_fips(k): v for k, v in raw_names.items()}
    name = county_names.get(fips3)
    return f"{name or fips3} ({fips3})"


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
def _load_tracts_geojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS set.")
    if str(gdf.crs) != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def _numeric_metrics(gdf: gpd.GeoDataFrame) -> list[str]:
    """
    Return numeric columns that make sense to map.
    Prefer a curated ordering for the MVP.
    """
    exclude = {"geometry"}
    numeric = [c for c in gdf.columns if c not in exclude and pd.api.types.is_numeric_dtype(gdf[c])]

    preferred = [
        # Buffer-based proxy metrics (new, preferred when present)
        "buffer_access_score",
        "buffer_amenity_total",
        "buffer_amenities_per_km2_total",

        # Legacy tract-inside metrics
        "coverage_score",
        "amenity_total",
        "amenities_per_km2_total",

        # Per-category legacy counts
        "count_groceries",
        "count_pharmacy",
        "count_parks",

        # Per-category buffer counts (if present)
        "buffer_count_groceries",
        "buffer_count_pharmacy",
        "buffer_count_parks",

        # Geometry/context
        "area_km2",
        "buffer_area_km2",
    ]
    ordered = [c for c in preferred if c in numeric]
    ordered += [c for c in numeric if c not in ordered]
    return ordered


def _render_folium_choropleth(gdf_wgs: gpd.GeoDataFrame, *, metric: str, height: int = 620) -> None:
    """
    Folium choropleth + hover tooltip, embedded via Streamlit components (no streamlit-folium).
    """
    import folium
    from folium.features import GeoJsonTooltip

    if gdf_wgs.empty:
        st.info("No tracts match the current filters.")
        return

    # center map from bounds
    minx, miny, maxx, maxy = gdf_wgs.total_bounds
    center_lat = (miny + maxy) / 2
    center_lon = (minx + maxx) / 2

    m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles="cartodbpositron")

    # choropleth needs key/value table
    data_df = gdf_wgs[["tract_geoid", metric]].copy()
    data_df[metric] = pd.to_numeric(data_df[metric], errors="coerce").fillna(0)

    folium.Choropleth(
        geo_data=gdf_wgs.__geo_interface__,
        data=data_df,
        columns=["tract_geoid", metric],
        key_on="feature.properties.tract_geoid",
        fill_opacity=0.75,
        line_opacity=0.2,
        legend_name=metric,
        nan_fill_opacity=0.0,
    ).add_to(m)

    # tooltip overlay (keeps choropleth simple)
    tooltip_fields = [c for c in ["tract_name", "county_fips", metric] if c in gdf_wgs.columns]
    tooltip_aliases: list[str] = []
    for c in tooltip_fields:
        if c == "tract_name":
            tooltip_aliases.append("Tract:")
        elif c == "county_fips":
            tooltip_aliases.append("County:")
        elif c == metric:
            tooltip_aliases.append(f"{metric}:")
        else:
            tooltip_aliases.append(f"{c}:")

    folium.GeoJson(
        gdf_wgs.__geo_interface__,
        name="Hover layer",
        style_function=lambda _: {"fillOpacity": 0.0, "weight": 0.6},
        highlight_function=lambda _: {"weight": 2.0},
        tooltip=GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases, localize=True),
    ).add_to(m)

    folium.LayerControl(collapsed=True).add_to(m)
    components.html(m.get_root().render(), height=height)


def render() -> None:
    st.title("Map Explorer")
    region_label = getattr(CFG, "region_label", "Atlas region")
    st.caption(f"Interactive exploration of tract-level access metrics for {region_label}.")

    tracts_geojson = CFG.curated_dir / "mart_access_tracts.geojson"
    _require_artifact(tracts_geojson, label="Modeled tracts GeoJSON (mart_access_tracts.geojson)")

    gdf = _load_tracts_geojson(tracts_geojson)
    # Normalize county_fips for consistent labels/filtering across data sources
    artifact_county_names = _county_name_map_from_df(gdf)
    cfg_county_names = {
        _norm_county_fips(k): v
        for k, v in (getattr(CFG, "county_names", {}) or {}).items()
    }
    county_label_map = {**cfg_county_names, **artifact_county_names}  # artifact wins

    # Minimum schema checks
    required = {"tract_geoid", "county_fips", "tract_name", "geometry"}
    missing = required - set(gdf.columns)
    if missing:
        st.error(f"`mart_access_tracts.geojson` missing expected columns: {sorted(missing)}")
        st.stop()

    metrics = _numeric_metrics(gdf)
    if not metrics:
        st.error("No numeric metric columns found to map.")
        st.stop()

    # Sidebar controls
    st.sidebar.header("Map filters")

    county_options = ["ALL"] + sorted(gdf["county_fips"].dropna().astype(str).unique().tolist())
    county_choice = st.sidebar.selectbox(
        "County",
        options=county_options,
        format_func=lambda x: (
            "All counties"
            if x == "ALL"
            else f"{county_label_map.get(_norm_county_fips(x), _norm_county_fips(x))} ({_norm_county_fips(x)})"
        ),
        index=0,
    )

    default_metric_candidates = [
        "buffer_amenities_per_km2_total",  # preferred if buffer metrics exist
        "buffer_access_score",
        "amenities_per_km2_total",         # legacy fallback
        "amenity_total",
        "coverage_score",
    ]
    default_metric = next((m for m in default_metric_candidates if m in metrics), metrics[0])
    default_index = metrics.index(default_metric)

    metric = st.sidebar.selectbox("Metric", options=metrics, index=default_index)

    min_score = 0
    if "coverage_score" in gdf.columns:
        max_score = len(getattr(CFG, "osm_categories", {})) or 3
        # (optional) guard: if data contains a higher score, let the slider reach it
        observed = int(pd.to_numeric(gdf["coverage_score"], errors="coerce").fillna(0).max())
        max_score = max(max_score, observed)

        min_score = int(st.sidebar.slider("Min coverage score", 0, max_score, 0))

    min_total = 0
    if "amenity_total" in gdf.columns:
        max_total = int(pd.to_numeric(gdf["amenity_total"], errors="coerce").fillna(0).max())
        min_total = int(st.sidebar.slider("Min total amenities", 0, max(1, max_total), 0))

    # --- Optional comparability filter (UI only; does not change artifacts) ---
    hide_large_tracts = False
    max_area_km2_filter: float | None = None

    if "area_km2" in gdf.columns:
        # Base area slider on county selection only (before min_score/min_total), so it's easy to reason about
        area_scope = gdf.copy()
        if county_choice != "ALL":
            area_scope = area_scope[area_scope["county_fips"] == county_choice].copy()

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

    top_n = int(st.sidebar.slider("Top / Bottom N", 5, 50, 15))

    # Apply filters
    view = gdf.copy()
    if county_choice != "ALL":
        view = view[view["county_fips"] == county_choice].copy()

    if "coverage_score" in view.columns:
        view = view[pd.to_numeric(view["coverage_score"], errors="coerce").fillna(0) >= min_score].copy()

    if "amenity_total" in view.columns:
        view = view[pd.to_numeric(view["amenity_total"], errors="coerce").fillna(0) >= min_total].copy()

    if hide_large_tracts and max_area_km2_filter is not None and "area_km2" in view.columns:
        view = view[
            pd.to_numeric(view["area_km2"], errors="coerce").fillna(float("inf")) <= max_area_km2_filter
        ].copy()

    # KPIs
    c1, c2, c3 = st.columns(3)
    c1.metric("Tracts in view", f"{len(view):,}")

    if "amenity_total" in view.columns:
        c2.metric("Amenities (sum)", f"{int(view['amenity_total'].fillna(0).sum()):,}")
    else:
        c2.metric("Amenities (sum)", "n/a")

    if "coverage_score" in view.columns:
        covered = int((view["coverage_score"].fillna(0) >= 1).sum())
        pct = (covered / len(view) * 100.0) if len(view) else 0.0
        c3.metric("Tracts with ≥1 category", f"{covered:,}", f"{pct:.1f}%")
    else:
        c3.metric("Tracts with ≥1 category", "n/a")

    # Explanation (kept collapsed by default)
    with st.expander("How to interpret coverage_score", expanded=False):
        max_score = len(getattr(CFG, "osm_categories", {})) or 3
        st.markdown(
            f"""
    **coverage_score (0–{max_score})** is a *category presence* proxy per tract.

    - **1 point per category** if the tract contains **≥1** OpenStreetMap POI **node** for that category.
    - This is **not** travel-time access. Dense areas can score lower if some categories are missing as nodes, while very large tracts can score higher if they contain at least one POI in each category.

    For “15-minute access” semantics, use buffer/isochrone-based metrics (planned upgrade).
    """
        )

    if "buffer_access_score" in view.columns:
            with st.expander("How to interpret buffer_access_score (15-minute proxy)", expanded=False):
                buf_max_score = max(
                    len(getattr(CFG, "osm_categories", {})) or 3,
                    int(pd.to_numeric(view["buffer_access_score"], errors="coerce").fillna(0).max()),
                )
                buffer_meters = getattr(CFG, "buffer_meters", 800)

                st.markdown(
                    f"""
    **buffer_access_score (0–{buf_max_score})** is a *nearby access proxy* per tract.

    - **1 point per category** if at least one POI is within **{buffer_meters:,} m** of the tract centroid.
    - This usually aligns better with intuition than “inside tract polygon” for dense urban areas.

    It is still an approximation (centroid + straight-line distance), not a street-network travel-time metric.
    """
            )

    # Map
    st.subheader("Choropleth map")
    if metric not in view.columns:
        st.warning(f"Selected metric `{metric}` is not present in the GeoJSON.")
    else:
        _render_folium_choropleth(view, metric=metric, height=620)

    # Distribution
    st.subheader("Metric distribution")
    series = pd.to_numeric(view[metric], errors="coerce").fillna(0)
    fig = px.histogram(pd.DataFrame({metric: series}), x=metric, nbins=30, title=f"Distribution: {metric}")
    st.plotly_chart(fig, width="stretch")

    # Top / Bottom
    st.subheader("Top / Bottom tracts")
    cols = [c for c in ["tract_geoid", "tract_name", "county_fips", metric] if c in view.columns]
    table = view[cols].copy()
    table["county_fips"] = table["county_fips"].astype(str)

    left, right = st.columns(2)
    with left:
        st.markdown(f"**Top {top_n}**")
        st.dataframe(table.sort_values(metric, ascending=False).head(top_n), width="stretch")

    with right:
        st.markdown(f"**Bottom {top_n}**")
        st.dataframe(table.sort_values(metric, ascending=True).head(top_n), width="stretch")


if __name__ == "__main__":
    render()