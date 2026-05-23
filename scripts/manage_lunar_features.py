#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Add REPO_ROOT to sys.path to import backend modules
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.dependency_helpers import resolve_workspace_root
from backend.services.nomenclature_service import NomenclatureService, clean_name, ensure_nomenclature_schema

def list_features(service: NomenclatureService, pattern: str, feature_type: str | None) -> None:
    """List features matching a pattern and optionally a type."""
    # We use search_fuzzy which internally handles the pattern matching.
    # Increasing limit to 100 to show more results if needed.
    features = service.search_fuzzy(query=pattern, feature_type=feature_type, limit=100)
    
    if not features:
        print(f"No features matching '{pattern}' found" + (f" with type '{feature_type}'" if feature_type else "") + ".")
        return
    
    # Print header
    print(f"{'ID':<10} {'Name':<35} {'Type':<20} {'Diam (km)':<12} {'Importance'}")
    print("-" * 90)
    
    for f in features:
        fid = f.get("feature_id", "N/A")
        name = f.get("name", "Unknown")
        ftype = f.get("feature_type") or ""
        diam = f.get("diameter_km")
        diam_str = f"{diam:.2f}" if diam is not None else "0.00"
        importance = f.get("importance_score", 0.0)
        
        print(f"{fid:<10} {name:<35} {ftype:<20} {diam_str:<12} {importance:.2f}")

def add_feature(
    db_path: Path, 
    name: str, 
    feature_type: str, 
    x: float, 
    y: float, 
    diameter: float, 
    description: str | None, 
    origin: str | None
) -> None:
    """Add a new feature to the database."""
    ensure_nomenclature_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        # Use diameter as importance score if not provided, same as ingest-nomenclature.py
        importance = float(diameter)
        
        # Insert into main table
        cur = conn.execute(
            """
            INSERT INTO lunar_features (
                name, clean_name, feature_type, diameter_km, importance_score, 
                description, center_x, center_y, origin_description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, clean_name(name), feature_type, diameter, importance, description, x, y, origin)
        )
        feature_id = cur.lastrowid
        
        # Insert into RTree for spatial queries
        # (min_x, max_x, min_y, max_y) - using point extent
        conn.execute(
            "INSERT INTO lunar_features_rtree (feature_id, min_x, max_x, min_y, max_y) VALUES (?, ?, ?, ?, ?)",
            (feature_id, x, x, y, y)
        )
        
        # FTS5 table (lunar_features_fts) is automatically updated via triggers 
        # defined in ensure_nomenclature_schema.
        
        conn.commit()
        print(f"Successfully added feature '{name}' (ID: {feature_id}) at ({x}, {y}).")
        
    except sqlite3.IntegrityError as e:
        print(f"Error: Could not add feature. It might already exist or data is invalid. ({e})")
        sys.exit(1)
    finally:
        conn.close()

def main() -> int:
    parser = argparse.ArgumentParser(description="Lunar Analyst Nomenclature CLI")
    parser.add_argument("--db", type=Path, help="Override path to scenario_catalog.db")
    subparsers = parser.add_subparsers(dest="verb", required=True, help="Action to perform")

    # List verb
    list_parser = subparsers.add_parser("list", help="List lunar features matching a name pattern")
    list_parser.add_argument("pattern", help="Partial or full name to search for")
    list_parser.add_argument("--type", help="Filter by feature type (e.g., Crater, Mare, Mons)")

    # Add verb
    add_parser = subparsers.add_parser("add", help="Add a new lunar feature entry")
    add_parser.add_argument("name", help="Display name of the feature")
    add_parser.add_argument("type", help="Feature type classification")
    add_parser.add_argument("x", type=float, help="Center X coordinate in ESRI:103878 (Stereographic)")
    add_parser.add_argument("y", type=float, help="Center Y coordinate in ESRI:103878 (Stereographic)")
    add_parser.add_argument("--diameter", type=float, default=0.0, help="Feature diameter in km")
    add_parser.add_argument("--description", help="Brief description or notes")
    add_parser.add_argument("--origin", help="Description of the name's origin")

    args = parser.parse_args()

    # Resolve database path
    if args.db:
        db_path = args.db.expanduser().resolve()
    else:
        workspace_root = resolve_workspace_root()
        db_path = (workspace_root / "scenario_catalog.db").resolve()

    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    service = NomenclatureService(db_path)

    if args.verb == "list":
        list_features(service, args.pattern, args.type)
    elif args.verb == "add":
        add_feature(
            db_path, 
            args.name, 
            args.type, 
            args.x, 
            args.y, 
            args.diameter, 
            args.description, 
            args.origin
        )

    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
