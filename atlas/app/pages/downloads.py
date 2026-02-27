from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import streamlit as st

from atlas.config import CFG


@dataclass(frozen=True)
class Artifact:
    label: str
    path: Path
    mime: str


def _fmt_bytes(n: int) -> str:
    # simple human-readable formatter
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n/1024:.2f} {unit}"
        n //= 1  # no-op for clarity
    return f"{n} B"


def _file_meta(path: Path) -> str:
    stat = path.stat()
    size = stat.st_size
    mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    # size formatting (keep simple/consistent)
    if size < 1024:
        size_str = f"{size} B"
    elif size < 1024 * 1024:
        size_str = f"{size/1024:.1f} KB"
    else:
        size_str = f"{size/(1024*1024):.2f} MB"
    return f"{size_str} • modified {mtime}"


@st.cache_data(show_spinner=False)
def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _artifact_card(a: Artifact) -> None:
    col1, col2 = st.columns([3, 2])
    with col1:
        st.markdown(f"**{a.label}**")
        st.code(str(a.path.relative_to(CFG.repo_root)) if a.path.is_absolute() else str(a.path), language="text")
        if a.path.exists():
            st.caption(_file_meta(a.path))
        else:
            st.warning("File not found. Run the pipeline refresh to generate it.")

    with col2:
        if a.path.exists():
            st.download_button(
                label="Download",
                data=_read_bytes(a.path),
                file_name=a.path.name,
                mime=a.mime,
                width="stretch",
            )


def render() -> None:
    st.title("Downloads")
    st.caption("Export curated and modeled artifacts produced by the Atlas pipeline.")

    st.info(
        "If files are missing, run:\n"
        "```bash\n"
        ".venv/bin/python -m atlas.pipeline.refresh\n"
        "```"
    )

    curated = [
        Artifact("dim_tracts (GeoJSON)", CFG.curated_dir / "dim_tracts.geojson", "application/geo+json"),
        Artifact("fact_amenities (GeoJSON)", CFG.curated_dir / "fact_amenities.geojson", "application/geo+json"),
    ]

    qa = [
        Artifact("QA report (Markdown)", CFG.curated_dir / "qa_report.md", "text/markdown"),
        Artifact("QA report (JSON)", CFG.curated_dir / "qa_report.json", "application/json"),
    ]

    modeled = [
        Artifact("mart_access_long (CSV)", CFG.curated_dir / "mart_access_long.csv", "text/csv"),
        Artifact("mart_access_wide (CSV)", CFG.curated_dir / "mart_access_wide.csv", "text/csv"),
        Artifact("mart_access_tracts (GeoJSON)", CFG.curated_dir / "mart_access_tracts.geojson", "application/geo+json"),
        Artifact("Model report (JSON)", CFG.curated_dir / "model_report.json", "application/json"),
    ]

    st.subheader("Modeled outputs")
    for a in modeled:
        _artifact_card(a)
        st.divider()

    st.subheader("QA / QC artifacts")
    for a in qa:
        _artifact_card(a)
        st.divider()

    st.subheader("Curated inputs")
    for a in curated:
        _artifact_card(a)
        st.divider()


if __name__ == "__main__":
    render()