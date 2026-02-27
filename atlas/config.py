from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


_FIPS3 = re.compile(r"^\d{3}$")
_GEOID5 = re.compile(r"^\d{5}$")


def _split_csv(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


@dataclass(frozen=True)
class AtlasConfig:
    # --- Region identity ---
    region_slug: str = "miami_metro"
    region_label: str = "Miami Metro (Miami-Dade, Broward, Palm Beach)"
    max_counties: int = 30

    # --- Paths ---
    repo_root: Path = Path(__file__).resolve().parents[1]
    data_dir: Path = field(init=False)
    raw_dir: Path = field(init=False)
    curated_dir: Path = field(init=False)

    # --- Geography ---
    state_fips: str = "12"  # Florida
    county_geoids: tuple[str, ...] = ("12086", "12011", "12099")  # SSCCC
    county_fips: tuple[str, ...] = field(init=False)  # CCC

    # Optional: display names keyed by 3-digit county_fips
    county_names: dict[str, str] = field(default_factory=lambda: {
        "086": "Miami-Dade",
        "011": "Broward",
        "099": "Palm Beach",
    })

    # --- TIGER ---
    tiger_year: int = 2023
    tiger_base_url: str = "https://www2.census.gov/geo/tiger"

    # --- Spatial ---
    projected_crs: str = "EPSG:26917"  # UTM 17N (South FL) - override for other regions
    buffer_meters: float = 800.0

    # --- OSM categories ---
    osm_categories: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "groceries": {"shop": "supermarket"},
        "pharmacy": {"amenity": "pharmacy"},
        "parks": {"leisure": "park"},
    })

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", self.repo_root / "data")
        object.__setattr__(self, "raw_dir", self.data_dir / "raw" / self.region_slug)
        object.__setattr__(self, "curated_dir", self.data_dir / "curated" / self.region_slug)

        # Validate county list
        if not self.county_geoids:
            raise ValueError("county_geoids cannot be empty.")

        if len(self.county_geoids) > self.max_counties:
            raise ValueError(f"Too many counties: {len(self.county_geoids)} (max {self.max_counties}).")

        bad = [g for g in self.county_geoids if not _GEOID5.match(g)]
        if bad:
            raise ValueError(f"Invalid county GEOIDs (expected 5 digits): {bad}")

        # Validate state prefix consistency
        bad_state = [g for g in self.county_geoids if g[:2] != self.state_fips]
        if bad_state:
            raise ValueError(
                f"county_geoids must start with state_fips={self.state_fips}. Offenders: {bad_state}"
            )

        fips = tuple(g[2:] for g in self.county_geoids)
        bad3 = [c for c in fips if not _FIPS3.match(c)]
        if bad3:
            raise ValueError(f"Invalid derived county_fips: {bad3}")

        object.__setattr__(self, "county_fips", fips)


def load_config() -> AtlasConfig:
    """
    Load config from:
      1) ATLAS_CONFIG toml file (optional)
      2) env var overrides (optional)
      3) defaults
    """
    data: dict[str, object] = {}

    cfg_path = os.getenv("ATLAS_CONFIG")
    if cfg_path:
        p = Path(cfg_path).expanduser().resolve()
        with p.open("rb") as f:
            raw = tomllib.load(f)
        if not isinstance(raw, dict):
            raise ValueError("ATLAS_CONFIG did not parse into a dict.")
        data.update(raw)

    # Env overrides (simple + robust)
    if os.getenv("ATLAS_REGION_SLUG"):
        data["region_slug"] = os.getenv("ATLAS_REGION_SLUG")
    if os.getenv("ATLAS_REGION_LABEL"):
        data["region_label"] = os.getenv("ATLAS_REGION_LABEL")
    if os.getenv("ATLAS_STATE_FIPS"):
        data["state_fips"] = os.getenv("ATLAS_STATE_FIPS")
    if os.getenv("ATLAS_COUNTY_GEOIDS"):
        data["county_geoids"] = tuple(_split_csv(os.getenv("ATLAS_COUNTY_GEOIDS", "")))
    if os.getenv("ATLAS_PROJECTED_CRS"):
        data["projected_crs"] = os.getenv("ATLAS_PROJECTED_CRS")

    return AtlasConfig(**data)  # type: ignore[arg-type]


CFG = load_config()