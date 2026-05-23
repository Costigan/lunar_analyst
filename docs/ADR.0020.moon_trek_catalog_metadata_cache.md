# ADR 0020: Persist Moon Trek Catalog Metadata Cache in Primary App Database

- Status: Accepted
- Date: 2026-03-09
- Owners: Architecture (Codex), Product/Implementation (Gemini)
- Related: `docs/DESIGN.md`, `docs/ADR.0002.scenario_filesystem_and_catalog.md`, `docs/ADR.0019.unified_tool_model.md`

## Context

Lunar Analyst currently fetches Moon Trek catalog metadata from remote Trek services and keeps it in process-memory TTL caches.
This improves repeated access during one backend process lifetime, but cache state is lost on backend restart.

This creates avoidable friction for the layer menu experience:
- cold-start catalog fetch latency after restart;
- reduced resilience during temporary Trek service outages;
- repeated remote catalog traffic for data that changes slowly.

At the same time, we do not want to persist large remote payloads or turn Moon Trek into authoritative local product state.
The requirement is specifically to cache only metadata needed to drive Moon Trek layer discovery/menu UX.

## Decision

Persist Moon Trek **catalog metadata only** in SQLite in the app primary database as a durable cache layer.

1. Scope of persisted data (allowed):
- product identity and display metadata required for layer menu/search/filter/sort (for example label/title/description/service-type/bounds-like summary fields when available);
- cache freshness metadata (`fetched_at_utc`, `expires_at_utc`, optional `source_updated_at` when provided);
- cache provenance metadata (source endpoint/version markers as needed for invalidation).

2. Scope explicitly not persisted by this ADR:
- remote product files/archives/tiles;
- full feature geometry payloads from Trek feature services/download fallbacks;
- scenario-local product registrations derived from Moon Trek.

3. Cache model:
- keep current in-memory cache behavior as L1 (fast per-process cache);
- add SQLite-backed metadata cache as L2 (survives process restarts);
- use stale-while-revalidate semantics: return non-expired cached metadata immediately, refresh in background when appropriate.

4. API contract expectations:
- existing Trek list/search endpoints keep their external shape;
- `force_refresh=true` remains a bypass for normal cache use;
- response `cached` semantics should remain meaningful and may represent L1 or L2 origin.

5. Ownership model:
- Moon Trek metadata cache remains non-authoritative integration state;
- scenario DB (`scenario.db`) remains authoritative only for scenario-owned products/files/layers.

## Rationale

- Delivers faster and more stable Moon Trek layer-menu UX across backend restarts.
- Improves operational resilience when Trek APIs are slow or temporarily unavailable.
- Avoids data bloat and complexity by persisting only lightweight catalog metadata.
- Preserves architecture boundaries: external catalog cache is not merged with scenario-owned artifact state.

## Consequences

Positive:
- lower perceived startup and first-open latency for Moon Trek layer discovery;
- reduced repeated remote catalog requests for slow-changing data;
- better degraded-mode behavior using recent cached metadata.

Tradeoffs:
- requires schema migration and cache lifecycle management (expiry/invalidation/cleanup);
- introduces cache-staleness risk if refresh policy is too permissive;
- adds repository/service code paths and test surface.

## Out of Scope

- Caching Moon Trek tiles or feature geometries.
- Replacing live feature proxy behavior with persistent local feature stores.
- Converting Moon Trek catalog data into scenario-owned products automatically.

## Follow-on Tasks

1. Add DB schema for Moon Trek catalog metadata cache (idempotent, versioned migration).
2. Add repository/service layer for read-through + write-through metadata cache operations.
3. Keep in-memory TTL cache and layer it over SQLite cache (L1/L2).
4. Add stale-while-revalidate policy and explicit `force_refresh` bypass handling.
5. Add observability:
- cache hit/miss/stale-refresh metrics/log fields;
- last-successful-refresh timestamp for diagnostics.
6. Add tests:
- repository round-trip and expiry behavior;
- endpoint behavior for cache hit/miss/force-refresh;
- restart resilience (L2 survives backend restart);
- fallback behavior when remote fetch fails but stale cache exists.

## Risk Controls

- Treat cached Moon Trek metadata as non-authoritative and refreshable.
- Bound stored fields to menu-driving metadata only.
- Add periodic/size-based cleanup to prevent unbounded growth.
- Because schema changes are involved, implementation requires explicit human approval per project safety policy.
