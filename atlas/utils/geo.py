from __future__ import annotations

import geopandas as gpd


def require_crs(gdf: gpd.GeoDataFrame, *, name: str = "GeoDataFrame") -> None:
    """Fail fast if CRS is missing."""
    if gdf.crs is None:
        raise ValueError(f"{name} CRS is None; set/assign a CRS before reprojection or distance/area ops.")


def drop_empty_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Drop null/empty geometries.
    Note: invalid geometries are NOT dropped (handle/report in QA/QC).
    """
    out = gdf.copy()
    out = out[out.geometry.notna()]
    out = out[~out.geometry.is_empty]
    return out


def to_crs_safe(gdf: gpd.GeoDataFrame, crs: str, *, name: str = "GeoDataFrame") -> gpd.GeoDataFrame:
    """Reproject after an explicit CRS check."""
    require_crs(gdf, name=name)
    return gdf.to_crs(crs)


def bounds_center_wgs84(gdf: gpd.GeoDataFrame) -> tuple[float, float]:
    """Return (lat, lon) center from bounds for initializing Folium maps."""
    require_crs(gdf)
    wgs = gdf.to_crs("EPSG:4326")
    minx, miny, maxx, maxy = wgs.total_bounds
    return (miny + maxy) / 2, (minx + maxx) / 2