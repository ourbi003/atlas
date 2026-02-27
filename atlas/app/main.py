from __future__ import annotations

import streamlit as st

from atlas.config import CFG

def _render_page(page: str) -> None:
    """
    Route to the selected page module.
    Each page module must expose a `render()` function.
    """
    if page == "Home":
        from atlas.app import home
        home.render()
        return

    if page == "Map Explorer":
        from atlas.app.pages import map_explorer
        map_explorer.render()
        return

    if page == "QA / QC":
        from atlas.app.pages import qa_qc
        qa_qc.render()
        return

    if page == "Downloads":
        from atlas.app.pages import downloads
        downloads.render()
        return

    st.error(f"Unknown page selection: {page}")


def main() -> None:
    # Must be called exactly once, and before any other Streamlit calls.
    st.set_page_config(page_title="Atlas", layout="wide")

    st.sidebar.title("Atlas")
    st.sidebar.caption(getattr(CFG, "region_label", "Urban + GIS Analytics"))

    page = st.sidebar.radio(
        "Navigate",
        options=("Home", "Map Explorer", "QA / QC", "Downloads"),
        index=0,
    )

    try:
        _render_page(page)
    except ModuleNotFoundError as e:
        st.error(
            "A required page module could not be imported.\n\n"
            f"**Details:** `{e}`\n\n"
            "Check that these files exist:\n"
            "- `atlas/app/home.py`\n"
            "- `atlas/app/pages/map_explorer.py`\n"
            "- `atlas/app/pages/qa_qc.py`\n"
            "- `atlas/app/pages/downloads.py`\n"
            "\nAlso confirm `__init__.py` exists in:\n"
            "- `atlas/app/`\n"
            "- `atlas/app/pages/`"
        )
    except Exception as e:
        st.exception(e)


if __name__ == "__main__":
    main()