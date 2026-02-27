from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest


def test_ensure_dir_creates_nested(tmp_path: Path) -> None:
    from atlas.utils.io import ensure_dir

    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()

    ensure_dir(nested)
    assert nested.exists()
    assert nested.is_dir()


def test_write_json_and_read_json_roundtrip(tmp_path: Path) -> None:
    from atlas.utils.io import read_json, write_json

    payload: dict[str, Any] = {
        "project": "Atlas",
        "ok": True,
        "n": 3,
        "items": ["groceries", "pharmacy", "parks"],
        "meta": {"county_fips": ["086", "011", "099"]},
    }

    out = tmp_path / "out" / "report.json"
    write_json(out, payload)
    assert out.exists()

    loaded = read_json(out)
    assert loaded == payload


def test_download_file_writes_bytes_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Unit test: verify download_file writes bytes to disk.
    We monkeypatch urllib.request.urlopen to avoid any real network.
    """
    import atlas.utils.io as io_mod

    class _DummyResp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> "_DummyResp":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def _fake_urlopen(request: Any, *, timeout: float | None = None, **_extra: Any) -> _DummyResp:
        # Touch params so IDE knows they are intentionally accepted/used.
        assert request is not None
        _ = timeout  # explicitly unused beyond sanity
        return _DummyResp(b"hello-atlas")

    # Patch urlopen used inside atlas.utils.io
    monkeypatch.setattr(io_mod.urllib.request, "urlopen", _fake_urlopen)

    dest = tmp_path / "downloads" / "file.bin"

    # Build kwargs for download_file based on its actual signature
    dl_kwargs: dict[str, Any] = {}
    sig = inspect.signature(io_mod.download_file)
    if "overwrite" in sig.parameters:
        dl_kwargs["overwrite"] = True
    if "timeout_s" in sig.parameters:
        dl_kwargs["timeout_s"] = 1
    if "user_agent" in sig.parameters:
        dl_kwargs["user_agent"] = "pytest"

    io_mod.download_file("https://example.com/fake", dest, **dl_kwargs)  # type: ignore[arg-type]
    assert dest.exists()
    assert dest.read_bytes() == b"hello-atlas"



def test_require_crs_raises_when_missing() -> None:
    """
    Unit test: require_crs should fail loudly when GeoDataFrame CRS is None.
    """
    geopandas = pytest.importorskip("geopandas")
    shapely_geom = pytest.importorskip("shapely.geometry")

    from atlas.utils.geo import require_crs

    gdf = geopandas.GeoDataFrame(
        {"id": [1]},
        geometry=[shapely_geom.Point(0, 0)],
        crs=None,
    )

    with pytest.raises(Exception):
        # Support either require_crs(gdf) or require_crs(gdf, name="...") patterns
        sig = inspect.signature(require_crs)
        if "name" in sig.parameters:
            require_crs(gdf, name="test_gdf")  # type: ignore[arg-type]
        else:
            require_crs(gdf)  # type: ignore[misc]


def test_require_crs_passes_when_set() -> None:
    """
    Unit test: require_crs should not raise when CRS is set.
    """
    geopandas = pytest.importorskip("geopandas")
    shapely_geom = pytest.importorskip("shapely.geometry")

    from atlas.utils.geo import require_crs

    gdf = geopandas.GeoDataFrame(
        {"id": [1]},
        geometry=[shapely_geom.Point(-80.1918, 25.7617)],
        crs="EPSG:4326",
    )

    sig = inspect.signature(require_crs)
    if "name" in sig.parameters:
        require_crs(gdf, name="test_gdf")  # type: ignore[arg-type]
    else:
        require_crs(gdf)  # type: ignore[misc]


def test_to_crs_safe_changes_crs() -> None:
    """
    Unit test: to_crs_safe should reproject when CRS is set.
    """
    geopandas = pytest.importorskip("geopandas")
    shapely_geom = pytest.importorskip("shapely.geometry")

    from atlas.utils.geo import to_crs_safe

    gdf = geopandas.GeoDataFrame(
        {"id": [1]},
        geometry=[shapely_geom.Point(-80.1918, 25.7617)],
        crs="EPSG:4326",
    )

    projected = to_crs_safe(gdf, "EPSG:26917", name="test_gdf")
    assert projected.crs is not None
    assert str(projected.crs) == "EPSG:26917"