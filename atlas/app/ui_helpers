from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd
import streamlit as st


def norm_county_fips(v: object) -> str:
    """
    Normalize county FIPS to a 3-digit string.
    Handles: 86, "86", "086", "86.0"
    """
    if pd.isna(v):
        return ""

    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]

    return s.zfill(3) if s.isdigit() else s


def county_name_map_from_df(df: pd.DataFrame) -> dict[str, str]:
    """
    Build a county_fips -> county_name map from an artifact dataframe if available.
    Expects columns: county_fips, county_name
    """
    if "county_fips" not in df.columns or "county_name" not in df.columns:
        return {}

    tmp = df[["county_fips", "county_name"]].copy()
    tmp["county_fips"] = tmp["county_fips"].map(norm_county_fips)
    tmp["county_name"] = tmp["county_name"].astype(str).str.strip()

    tmp = tmp[
        (tmp["county_fips"] != "")
        & (tmp["county_name"] != "")
        & (tmp["county_name"].str.lower() != "nan")
    ].drop_duplicates(subset=["county_fips"])

    return dict(zip(tmp["county_fips"], tmp["county_name"]))

def require_artifact(path: Path, *, label: str) -> None:
    if not path.exists():
        st.error(
            f"Missing required artifact: `{path}` ({label}).\n\n"
            "Run the pipeline first:\n"
            "```bash\n"
            ".venv/bin/python -m atlas.pipeline.refresh\n"
            "```"
        )
        st.stop()


def merge_county_name_maps(
    *,
    cfg_names: Mapping[str, str] | None,
    artifact_names: Mapping[str, str] | None,
) -> dict[str, str]:
    """
    Merge county name sources. Artifact wins (data-driven, region-agnostic).
    """
    cfg_names = cfg_names or {}
    artifact_names = artifact_names or {}

    cfg_norm = {norm_county_fips(k): v for k, v in cfg_names.items()}
    art_norm = {norm_county_fips(k): v for k, v in artifact_names.items()}

    return {**cfg_norm, **art_norm}


def format_county_option(value: object, label_map: Mapping[str, str]) -> str:
    s = str(value)
    if s == "ALL":
        return "All counties"
    f = norm_county_fips(s)
    name = label_map.get(f)
    return f"{name or f} ({f})"
