# ADR 0009: Path-First Product Identity with Immutable Internal IDs

- Status: Accepted
- Date: 2026-02-17
- Deciders: Lunar Analyst architecture/design owners
- Related: `docs/ADR.0002.scenario_filesystem_and_catalog.md`, `docs/ADR.0004.versioning_policy.md`, `docs/PLAN.md`

## Context

Scenario Explorer usability depends on users understanding products as files/folders, not opaque IDs. The current model is product-centric in several UI paths, which exposes internal identifiers (`product_id`) and metadata group labels instead of familiar filesystem names.

At the same time, the system needs robust references for:
- layer state and z-order persistence,
- lineage and job metadata,
- API compatibility and future evolution.

## Decision

Adopt a path-first product model:

1. User-facing identity is scenario-relative path (`relative_path` / collection path), rendered as filesystem names/folders in Explorer.
2. Internal immutable identifiers remain in DB (`product_id`, `file_id`) for referential integrity and stable joins.
3. Product metadata remains authoritative in `scenario.db`, layered on top of filesystem organization.
4. Multi-file products are first-class collection products represented by directory paths.

Examples:
- Single-file product: `dem.tif`, `hillshade.tif`.
- Multi-file product: `lightmaps/<series_name>/...`, `horizons/<set_name>/...`.

## Invariants

- No breaking changes to existing `/api/v1` contracts during transition (additive-only policy).
- Paths are scenario-root-relative, normalized, and traversal-safe.
- Display-only derivatives (for example `display/...`, `.cog.` intermediates) remain metadata-tracked but are hidden by default in primary Explorer views.
- CRS metadata remains explicit and unchanged by identity model shifts.

## Rationale

Benefits:
- Matches user mental model (filesystem-first).
- Reduces UI confusion and supports predictable filtering/selection.
- Preserves robust internal references across rename/move operations.

Tradeoffs:
- Requires reconciliation rules between filesystem state and DB metadata.
- Rename/move operations must be atomic across filesystem + DB metadata updates.
- Transitional UI/API support must avoid contract regressions.

## Consequences

Positive:
- Explorer can show `dem.tif`/`hillshade.tif` directly.
- Multi-file products can be presented as folders naturally.
- Internal IDs remain stable for layers/jobs/history.

Costs:
- Additional reconciliation logic and test coverage.
- More explicit policy for system/internal/display artifacts in UI.

## Out of Scope

- New API major version (`/api/v2`).
- Replacing internal IDs with paths in all storage tables.
- Full per-user virtualization/search preference systems.

## Implementation Notes

- Prefer path as canonical user key and unique logical identity per scenario.
- Keep immutable surrogate IDs internally.
- Add explicit rename/move operations that update both path metadata and filesystem paths in a coordinated operation.
