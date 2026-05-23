# ADR.0050: Local Lunar Nomenclature and Feature Navigation

- Status: Accepted
- Date: 2026-04-13
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0020.moon_trek_catalog_metadata_cache.md`, `docs/ADR.0035.typed_entity_memory_and_reference_resolution_v1.md`, `docs/ADR.0048.semantic_intent_family_extraction_and_property_mapping.md`

## Context

Lunar mission planning and analysis frequently require navigating to or identifying named features (e.g., "Shackleton Crater", "Malapert Massif", "Mare Crisium"). Currently, Lunar Analyst handles lunar features through:

- **Moon Trek Overlays**: Nomenclature can be added as visual layers, but features are not searchable entities within the local control plane.
- **RAG (Retrieval-Augmented Generation)**: The assistant can find mentions of features in scientific PDFs, but cannot deterministically map a name to a coordinate or extent for navigation.

As the application moves toward more complex scenario-based analysis, the lack of a local, structured source of truth for lunar landmarks creates friction in both the UI and assistant workflows.

## Problem

1. **Navigation Latency**: Relying on external feature services (like Moon Trek or ArcGIS) for simple name-to-coordinate resolution is slow and requires active internet connectivity.
2. **Assistant Unreliability**: The assistant cannot reliably "zoom to" or "center on" a named crater because it lacks a structured gazetteer. It often falls back to prose descriptions or imprecise RAG hits.
3. **Spatial Awareness**: The system cannot easily answer questions like "Which named features are within my current scenario extent?" without a local spatial index of nomenclature.

## Decision

Adopt a local, SQLite-based **Lunar Nomenclature Dataset** and implement a **Feature Navigation** service. This system will serve as the authoritative local source for lunar landmark resolution and map-based feature discovery.

### Decision Summary

1. **Local Gazetteer**: Store a curated subset of the IAU/USGS Gazetteer of Planetary Nomenclature in the existing workspace-global SQLite catalog database (`<workspace_root>/scenario_catalog.db`).
2. **Exact Name Resolution**: Provide `resolve_exact(name, feature_type?)` that requires an exact feature name and returns a single structured result or `not_found`.
3. **Fuzzy Name Search**: Provide `search_fuzzy(query, limit, feature_type?)` that returns a scored, sorted list of candidate matches.
4. **Nearby Search**: Provide `nearby(x, y, limit, feature_type?)` that returns features near a point, sorted by distance ascending.
5. **Importance-Based Search Tie-Breaking**: Use importance scoring only as a deterministic tie-breaker for fuzzy/search UI ranking and map labeling.
6. **Spatial Indexing**: Store projected feature location data in `ESRI:103878` with a spatial index (R-Tree) to support nearby and extent-based queries.
7. **Dynamic Map Layer**: Implement a specialized map layer for nomenclature that supports user-driven filtering by type and name.
8. **Deterministic Mapping**: Map resolved nomenclature queries to deterministic map navigation commands.
9. **Advanced Assistant Integration**: Implement `location_navigation` intent-family integration, entity-memory pinning, and radius-based proximity operations as part of primary delivery.

## Scope

In scope for this ADR:

- selection and ingestion of the USGS Gazetteer (IAU lunar subset),
- database schema with FTS5 and spatial indexing,
- `NomenclatureService` backend implementation (`resolve_exact`, `search_fuzzy`, `nearby`),
- assistant integration including `location_navigation` routing and entity-memory pinning,
- dynamic nomenclature vector layer with zoom-aware labeling,
- search-and-goto UI component in the web client,
- advanced proximity queries and optional RAG-enriched descriptions.

Out of scope:

- ingesting non-lunar planetary bodies,
- real-time synchronization with live USGS updates (static snapshots are sufficient for v1),
- custom user-defined "named locations" (handled by scenario markers).

## Operation Contract (Normative)

All nomenclature operations return records with this canonical shape:

```json
{
  "name": "Shackleton",
  "feature_type": "Crater",
  "location": {
    "kind": "point",
    "center": { "x": 0.0, "y": 0.0, "crs": "ESRI:103878" },
    "region": null
  },
  "description": "Impact crater centered near the lunar south pole."
}
```

If a region is available, `location.kind` is `region` and `location.region` contains `{min_x, min_y, max_x, max_y, crs}` in `ESRI:103878`.

Operations:

1. `resolve_exact(name: str, feature_type?: str)`:
   - name match must be exact after `clean_name` normalization.
   - returns one record or `not_found`.
2. `search_fuzzy(query: str, limit: int, feature_type?: str)`:
   - returns candidate list sorted by `match_score DESC`, then `importance_score DESC`, then `name ASC`.
3. `nearby(x: float, y: float, limit: int, feature_type?: str)`:
   - returns records sorted by `distance_m ASC`.
   - each record includes `distance_m`.

## Architecture and Flow

1. **Ingestion**: A maintenance script (`scripts/ingest-nomenclature.py`) converts the USGS CSV/GeoJSON into nomenclature tables inside `<workspace_root>/scenario_catalog.db`.
2. **Service**: `backend/services/nomenclature_service.py` provides:
   - `resolve_exact(name: str, feature_type: str | None = None)` -> exact match by normalized name.
   - `search_fuzzy(query: str, limit: int, feature_type: str | None = None)` -> scored and sorted candidate list.
   - `nearby(x: float, y: float, limit: int, feature_type: str | None = None)` -> nearest features sorted by distance.
   - `get_features_in_extent(extent: list[float], types: list[str])` -> powers the dynamic map layer.
3. **Assistant/UI Integration (minimal)**:
   - Search UI uses `search_fuzzy`.
   - "Go to feature" uses `resolve_exact` after user selection/confirmation.
   - "What's near here?" uses `nearby`.
4. **Visualization**: An OpenLayers `VectorSource` queries the backend dynamically, using importance scores to declutter labels based on zoom level.

## Data Schema (Proposed)

Nomenclature data is stored in `<workspace_root>/scenario_catalog.db` as additional global catalog tables.

```sql
-- Main metadata table
CREATE TABLE lunar_features (
    feature_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    clean_name TEXT NOT NULL, -- normalized for search
    feature_type TEXT,        -- Crater, Mons, Mare, etc.
    diameter_km REAL,
    importance_score REAL,    -- derived from diameter/rank
    description TEXT,
    -- projected point location in ESRI:103878 (meters)
    center_x REAL,
    center_y REAL,
    -- optional projected region extent in ESRI:103878
    min_x REAL,
    min_y REAL,
    max_x REAL,
    max_y REAL,
    origin_description TEXT
);

-- FTS5 table for fast fuzzy searching
CREATE VIRTUAL TABLE lunar_features_fts USING fts5(
    name,
    clean_name,
    feature_type,
    content='lunar_features',
    content_rowid='feature_id'
);

-- Spatial index for nearby/extent queries
CREATE VIRTUAL TABLE lunar_features_rtree USING rtree(
    feature_id,
    min_x, max_x,
    min_y, max_y
);

-- Dataset lineage metadata for ingestion reproducibility
CREATE TABLE IF NOT EXISTS nomenclature_dataset_metadata (
    dataset_key TEXT PRIMARY KEY,     -- e.g. "usgs_iau_moon"
    source_uri TEXT NOT NULL,
    source_revision TEXT,
    source_sha256 TEXT,
    ingested_at_utc TEXT NOT NULL
);
```

## Risks and Mitigations

- **Risk**: Label clutter on the map.
- **Mitigation**: Implement zoom-aware importance ranking (Tier 1 features at low zoom, Tier 3 at high zoom).
- **Risk**: Name ambiguity.
- **Mitigation**: `search_fuzzy` returns a sorted candidate list; Assistant prompts for clarification on low-confidence matches.
- **Risk**: Feature outside Scenario.
- **Mitigation**: `location.goto` calculates a safe extent and warns the user if the feature is outside current scenario DEM bounds.

## Detailed Implementation Plan

### Phase 1: Foundation & Data Ingestion
- [ ] Create `scripts/ingest-nomenclature.py` to process USGS IAU Gazetteer CSV.
- [ ] Implement name normalization and projection to `ESRI:103878`.
- [ ] Implement importance-score calculation (diameter-based + primary feature weighting).
- [ ] Create/refresh nomenclature tables in `<workspace_root>/scenario_catalog.db` with SQLite FTS5 and R-Tree indexes.

### Phase 2: Backend Service & API
- [ ] Implement `NomenclatureService` in `backend/services/nomenclature_service.py`.
- [ ] Implement `resolve_exact`, `search_fuzzy`, and `nearby` methods.
- [ ] Add API routes:
  - `GET /api/v1/nomenclature/search`
  - `GET /api/v1/nomenclature/resolve?name=...&type=...`
  - `GET /api/v1/nomenclature/nearby`
  - `GET /api/v1/nomenclature/features?extent=...&types=...` (for the map layer).
- [ ] Add unit tests for spatial resolution and search ranking.

### Phase 3: Assistant & Routing (Minimal)
- [ ] Wire assistant/tool calls directly to `resolve_exact`, `search_fuzzy`, and `nearby`.
- [ ] Keep prompt extraction minimal in initial wiring (name string + optional type filter), then layer on intent-family routing in Phase 5.
- [ ] Ensure deterministic fallback path: ambiguous fuzzy results require explicit user disambiguation.

### Phase 4: Frontend & Visualization
- [ ] Implement `NomenclatureLayer` in OpenLayers using `VectorSource` with a `loader` function.
- [ ] Implement client-side label decluttering based on `importance_score` and zoom level.
- [ ] Add a "Nomenclature" panel to the Activity Bar with:
  - Feature search bar with auto-complete.
  - Type-based visibility toggles (Craters, Mons, Maria, etc.).
- [ ] Implement `map.zoom_to_extent` event handling for navigation commands.

### Phase 5: Advanced Integration (In Scope)
- [ ] Introduce `location_navigation` intent family aligned with ADR 0048.
- [ ] Integrate ADR 0035 typed entity memory pinning.
- [ ] Append RAG-derived prose to feature descriptions when available.
- [ ] Add radius/advanced proximity operators (e.g., "within 20 km of X").

## Definition of Done

The nomenclature system is complete when:

- [ ] `<workspace_root>/scenario_catalog.db` contains populated nomenclature tables (`lunar_features`, `lunar_features_fts`, `lunar_features_rtree`).
- [ ] `NomenclatureService` implements `resolve_exact`, `search_fuzzy`, and `nearby` with the canonical response shape.
- [ ] Assistant can successfully execute "Go to Shackleton Crater" and "What is near the lander?".
- [ ] Assistant implements `location_navigation` routing, ADR 0035 entity-memory pinning, and radius-based proximity queries.
- [ ] UI search bar and visibility toggles are functional in the Analyst shell.
- [ ] `docs/DESIGN.md` includes a cross-reference to ADR 0050.
- [ ] Integration tests verify search, resolution, and navigation end-to-end.
