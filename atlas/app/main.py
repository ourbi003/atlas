from __future__ import annotations

import streamlit as st

from atlas.config import CFG


def main() -> None:
    st.set_page_config(page_title="Atlas", layout="wide")

    st.sidebar.title("Atlas")
    st.sidebar.caption(getattr(CFG, "region_label", "Urban + GIS Analytics"))

    pages = {
        "Home": ("atlas.app.home", "render"),
        "Map Explorer": ("atlas.app.pages.map_explorer", "render"),
        "QA / QC": ("atlas.app.pages.qa_qc", "render"),
        "Downloads": ("atlas.app.pages.downloads", "render"),
    }

    page = st.sidebar.radio("Navigate", options=tuple(pages.keys()), index=0)

    try:
        module_path, fn_name = pages[page]
        mod = __import__(module_path, fromlist=[fn_name])
        getattr(mod, fn_name)()
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
