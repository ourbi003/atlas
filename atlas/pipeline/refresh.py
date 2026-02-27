from __future__ import annotations

import argparse
from pathlib import Path

from atlas.config import CFG
from atlas.pipeline.ingest_osm import ingest_osm_amenities
from atlas.pipeline.ingest_tiger import ingest_tracts
from atlas.pipeline.model import build_access_mart
from atlas.pipeline.qa import run_qa


def _rel(p: Path) -> str:
    """Pretty-print paths relative to repo root when possible."""
    try:
        return str(p.relative_to(CFG.repo_root))
    except ValueError:
        return str(p)


def _osm_mode_label(*, include_ways: bool, include_relations: bool) -> str:
    parts = ["nodes"]
    if include_ways:
        parts.append("ways")
    if include_relations:
        parts.append("relations")
    return " + ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas.pipeline.refresh",
        description="Atlas pipeline runner: ingest -> QA -> model",
    )
    parser.add_argument("--force-tiger", action="store_true", help="Re-download TIGER tract zip.")
    parser.add_argument("--force-extract", action="store_true", help="Re-extract TIGER tract zip.")
    parser.add_argument("--force-osm", action="store_true", help="Re-fetch OSM amenities from Overpass.")
    parser.add_argument("--osm-timeout", type=int, default=180, help="Overpass request timeout seconds.")
    parser.add_argument(
        "--osm-include-ways",
        action="store_true",
        help="Include OSM ways (using Overpass `out center`) in addition to nodes. Slower, more realistic counts.",
    )
    parser.add_argument(
        "--osm-include-relations",
        action="store_true",
        help="Include OSM relations (using Overpass `out center`) in addition to nodes. Slowest / may increase timeouts.",
    )
    parser.add_argument(
        "--keep-unassigned",
        action="store_true",
        help="Keep amenities not assigned to a tract during modeling (default drops them).",
    )

    args = parser.parse_args(argv)

    print("=== Atlas refresh ===")
    print("repo_root:", _rel(CFG.repo_root))
    print("raw_dir  :", _rel(CFG.raw_dir))
    print("curated  :", _rel(CFG.curated_dir))
    print()

    print("[1/4] ingest_tiger")
    tracts = ingest_tracts(force_download=args.force_tiger, force_extract=args.force_extract)
    print(f"  tracts: {len(tracts):,}  crs={tracts.crs}")
    print(f"  wrote : {_rel(CFG.curated_dir / 'dim_tracts.geojson')}")
    print()

    print("[2/4] ingest_osm")
    print(
        "  mode     :",
        _osm_mode_label(
            include_ways=args.osm_include_ways,
            include_relations=args.osm_include_relations,
        ),
    )
    amenities = ingest_osm_amenities(
        force_fetch=args.force_osm,
        timeout_s=args.osm_timeout,
        include_ways=args.osm_include_ways,
        include_relations=args.osm_include_relations,
    )
    print(f"  amenities: {len(amenities):,}  crs={amenities.crs}")
    print(f"  wrote    : {_rel(CFG.curated_dir / 'fact_amenities.geojson')}")
    if "category" in amenities.columns:
        print("  by category:", amenities["category"].value_counts().to_dict())
    print()

    print("[3/4] qa")
    qa = run_qa()
    print(f"  wrote : {_rel(CFG.curated_dir / 'qa_report.json')}")
    print(f"  wrote : {_rel(CFG.curated_dir / 'qa_report.md')}")
    print("  amenities outside tracts:", qa["cross_checks"]["amenities_outside_tracts"])
    print()

    print("[4/4] model")
    outputs = build_access_mart(drop_unassigned_points=not args.keep_unassigned)
    print("  wrote :", _rel(outputs.long_csv))
    print("  wrote :", _rel(outputs.wide_csv))
    print("  wrote :", _rel(outputs.tracts_geojson))
    print("  wrote :", _rel(outputs.report_json))
    print()

    print("=== Done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())