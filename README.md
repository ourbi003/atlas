# Atlas — Urban + GIS Analytics (Tract-Level Access Metrics)

Atlas is a reproducible **GIS data pipeline + Streamlit dashboard** that models **tract-level “access” metrics** from:

- **US Census TIGER/Line** tract boundaries (dimension layer)
- **OpenStreetMap (OSM)** POI amenities from Overpass (fact layer)

It ships with a default configuration for **Miami Metro** (Miami-Dade, Broward, Palm Beach), but is designed so users can run the same pipeline for **any set of counties** (recommended ≤ 30) with **minimal/no code changes**.

---

## What Atlas does

### Pipeline outputs (artifacts)
Atlas generates a curated + modeled set of artifacts for a region:

- `dim_tracts.geojson` — tract polygons (+ county metadata)
- `fact_amenities.geojson` — OSM POI points (nodes and optionally ways/relations as centers)
- `qa_report.json` + `qa_report.md` — QA/QC summary and checks
- `mart_access_long.csv` — tract × category long-format counts/densities
- `mart_access_wide.csv` — one row per tract with access metrics
- `mart_access_tracts.geojson` — tract polygons joined to modeled metrics
- `model_report.json` — reproducibility report (config + stats + output paths)

### Streamlit app (UI)
The app provides:

- **Home**: KPIs + distribution charts + a lightweight Folium choropleth preview
- **Map Explorer**: interactive choropleth + distribution + top/bottom tracts
- **QA / QC**: report browsing + validation summaries + outside-tract POI map
- **Downloads**: one-click download of generated artifacts

---

## Intended purpose + inference guide (read this first)

Atlas is an **educational / portfolio** GIS pipeline + dashboard meant for:

- rapid, reproducible **tract-level exploratory analysis**
- comparing **relative patterns** of amenity presence/density across tracts
- validating data quality and coverage (QA/QC) for a region
- exploring **a specific OSM category taxonomy** (e.g., groceries/pharmacy/parks) — results only reflect what is included in the taxonomy

### What categories are counted (defaults)
Atlas only counts POIs that match the configured category taxonomy (`osm_categories` in `atlas/config.py`).

Default categories shipped with the repo:
- **groceries** → `shop=supermarket`
- **pharmacy** → `amenity=pharmacy`
- **parks** → `leisure=park`

If you change `osm_categories`, the score ranges and totals change accordingly (e.g., coverage scores become 0..N where N = number of categories).

### What you *can* infer from Atlas (reasonable)
- **Relative differences** between tracts in *this dataset*, for the selected categories (e.g., tract A has more POIs per km² than tract B).
- **Coverage gaps** in OSM POI mapping (e.g., missing categories in a tract).
- Whether a tract is likely to be **underserved in mapped POIs** under this category taxonomy.
- Whether results change materially when switching from:
  - *tract-inside* metrics (`coverage_score`, `amenity_total`) to
  - *centroid-buffer* metrics (`buffer_access_score`, `buffer_amenity_total`).

### What you *cannot* infer (do not claim)
- **True “15-minute access”** or travel time (no street network routing, no isochrones).
- **Causality** or policy conclusions (“this caused that”).
- **Service quality/capacity** (a POI exists ≠ it is accessible, affordable, open, or usable).
- **Equity or demographic conclusions** without adding population, income, transit, etc.
- Precise comparisons across far-away regions **unless `projected_crs` is set appropriately** (areas/distances can distort).

### Why some “weird” results can happen
- Tracts can be **very large** (wetlands/rural) and still score high if they contain ≥1 POI of each category.
- OSM representation varies: some amenities are mapped as **ways/relations** (polygons), not nodes.
- `buffer_*` metrics usually align better with intuition for dense areas, but remain a **centroid + Euclidean distance approximation**.

---

## Key metrics (how to interpret)

### `coverage_score` (legacy / tract-inside)
A simple *category presence* proxy:

- **1 point per category** if the tract polygon contains ≥1 POI for that category.
- `coverage_score` ranges from **0..N categories**.

### Buffer-based “15-minute proxy” metrics (preferred when present)
Atlas also computes centroid-buffer metrics (straight-line distance in meters):

- `buffer_access_score` (0..N): **1 point per category** if ≥1 POI is within `buffer_meters` of the tract centroid
- `buffer_amenity_total`: total POIs within that centroid buffer
- `buffer_amenities_per_km2_total`: density within the buffer’s circular area

---

## Project structure

Typical layout:

```text
.
├─ atlas/
│  ├─ app/                  # Streamlit pages
│  ├─ pipeline/             # ingest_tiger, ingest_osm, qa, model, refresh
│  ├─ utils/                # io + geo helpers
│  └─ config.py             # config loader (defaults + env + optional toml)
├─ data/
│  ├─ raw/<region_slug>/    # downloaded / cached raw inputs per region
│  └─ curated/<region_slug>/# curated + modeled artifacts per region
├─ tests/                   # unit + smoke tests
├─ streamlit_app.py         # Streamlit entrypoint
├─ requirements.txt
└─ requirements-lock.txt
```

---

## Installation

### 1) Create + activate a virtual environment
Example:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

If you want fully pinned reproducibility (exact versions):

```bash
pip install -r requirements-lock.txt
```

---

## Run the pipeline (default: Miami Metro)

```bash
.venv/bin/python -m atlas.pipeline.refresh
```

Artifacts will land in:

- `data/raw/miami_metro/`
- `data/curated/miami_metro/`

---

## Run Streamlit

```bash
.venv/bin/streamlit run streamlit_app.py
```

---

## Configure a new region (no code changes)

Atlas supports region overrides via:

- `ATLAS_CONFIG=/path/to/config.toml` (optional)
- environment variables (optional)
- defaults in `atlas/config.py`

### Option A — One-off region run via environment variables (recommended for testing)

The key concept: `region_slug` controls where data goes.  
If you set a new `ATLAS_REGION_SLUG`, you won’t overwrite Miami artifacts.

Example: Tampa Bay test run (Hillsborough, Pinellas, Pasco, Hernando)

```bash
ATLAS_REGION_SLUG=tampa_bay_test ATLAS_REGION_LABEL="Tampa Bay Test (Hillsborough, Pinellas, Pasco, Hernando)" ATLAS_STATE_FIPS=12 ATLAS_COUNTY_GEOIDS=12057,12103,12101,12053 ATLAS_PROJECTED_CRS=EPSG:26917; .venv/bin/python -m atlas.pipeline.refresh --force-osm --osm-include-ways --osm-include-relations && .venv/bin/streamlit run streamlit_app.py
```

This will write to:

- `data/raw/tampa_bay_test/`
- `data/curated/tampa_bay_test/`

When you stop using those env vars, you’re back to Miami defaults automatically.

### Option B — A reusable `.toml` config file

Create a file (example `tampa.toml`):

```toml
region_slug = "tampa_bay"
region_label = "Tampa Bay (Hillsborough, Pinellas, Pasco, Hernando)"
state_fips = "12"
county_geoids = ["12057","12103","12101","12053"]
projected_crs = "EPSG:26917"
buffer_meters = 800.0

[county_names]
"057" = "Hillsborough"
"103" = "Pinellas"
"101" = "Pasco"
"053" = "Hernando"

[osm_categories]
groceries = { shop = "supermarket" }
pharmacy  = { amenity = "pharmacy" }
parks     = { leisure = "park" }
```

Then run:

```bash
ATLAS_CONFIG=./tampa.toml .venv/bin/python -m atlas.pipeline.refresh
ATLAS_CONFIG=./tampa.toml .venv/bin/streamlit run streamlit_app.py
```

You can delete the `.toml` afterwards — it won’t corrupt Miami data because region outputs are isolated by `region_slug`.

---

## Important note: projected CRS

Atlas computes areas and buffer distances in a projected CRS.  
The default is:

- `EPSG:26917` (UTM 17N) — good for South Florida

For other US regions, you should override `projected_crs` to a local appropriate CRS (often a UTM zone for that area), otherwise:

- tract `area_km2` can be distorted
- buffer calculations can be less accurate

Atlas intentionally keeps this explicit and user-configurable rather than auto-picking UTM zones.

---

## OSM ingestion modes (nodes vs ways/relations)

You can optionally include **ways** and/or **relations** using Overpass `out center` (more realistic in cities, but slower and more likely to hit timeouts).

### Flags (pipeline)

```bash
.venv/bin/python -m atlas.pipeline.refresh --force-osm --osm-include-ways --osm-include-relations
```

Notes:

- ways/relations are converted to points using `center.lon/center.lat`
- raw cache files are mode-specific (so modes do not collide)

---

## QA / QC

Atlas generates a QA report that includes:

- geometry null/empty/invalid counts per layer
- amenity category counts + missing name %
- duplicate OSM UID checks
- cross-checks: points outside tract polygons and “zero amenities by category” tract counts

Artifacts:

- `data/curated/<region_slug>/qa_report.json`
- `data/curated/<region_slug>/qa_report.md`

---

## Tests

Atlas includes:

- **Unit tests**: small pure-function behavior checks (no network)
- **Smoke tests**: verify artifacts exist + load + match minimal schema

Run all tests:

```bash
.venv/bin/python -m pytest -q
```

### Smoke test behavior

By default, smoke tests **SKIP** when artifacts are missing (developer-friendly).  
To require artifacts (CI-friendly):

```bash
ATLAS_REQUIRE_ARTIFACTS=1 .venv/bin/python -m pytest -q
```

---

## Reproducibility notes

This repo includes:

- `requirements.txt` — runtime dependencies (curated list)
- `requirements-lock.txt` — fully pinned lock (from `pip freeze`)

Recommended approach for reviewers/users:

- start with `requirements.txt`
- use `requirements-lock.txt` when you need an exact recreation of your environment

---

## Data sources + attribution

- **TIGER/Line** (US Census Bureau): tract boundaries + county boundaries
- **OpenStreetMap** contributors (via Overpass API): amenity points

OSM usage is subject to Overpass availability and rate limits. Atlas includes:

- multiple Overpass endpoints
- retries/backoff behavior
- optional cached raw JSON per region + mode

---

## Known limitations (current MVP)

- Uses tract centroids + Euclidean buffers (not a routing network)
- OSM completeness varies by place and category
- Ways/relations ingestion can slow large regions or time out
- Tract-level aggregation can hide within-tract inequality

---

## Roadmap (high-value upgrades)

- true travel-time access (OSRM / Valhalla / isochrones)
- population-weighted centroids (Census population grids)
- tiled Overpass queries for large regions (reduce timeouts)
- richer category taxonomy + configurable category packs
- optional caching + artifact versioning for multi-run comparisons

---

## License

MIT — see `LICENSE`.

---

## Author

Omar Urbina

