# Phase 4.5.1b: Tranche 1 Prioritization and Scope Lock

Date: 2026-02-16
Input: `docs/PHASE4_5_LEGACY_ALGORITHM_INVENTORY.md`

## Decision
Tranche 1 scope is locked to:
- Horizon profile
- Horizon directory
- Lightmap time-series
- LOS/viewshed
- DEM-derived products (`hillshade`, `slope`, `roughness`, `aspect`, `TPI`, `TRI`)

## Ranked Migration Order (Mission Value + Dependency Risk)

| Rank | Capability | Include in tranche 1 | Mission value | Dependency risk | Rationale |
|---|---|---|---|---|---|
| 1 | Horizon directory generation | Yes | High | Medium | Foundational artifact for downstream lighting/visibility workflows; already partially callable via bridge. |
| 2 | Horizon profile generation | Yes | High | Medium | Required for point/patch analysis and validation workflows; directly tied to legacy horizon science claims. |
| 3 | DEM-derived products | Yes | High | Low-Med | Immediate utility for site screening and UI overlays; bounded compute complexity compared to lighting pipelines. |
| 4 | LOS/viewshed | Yes | High | Medium | Core mission-analysis capability; depends on explicit CRS and tolerance definitions but should land in same tranche. |
| 5 | Lightmap time-series | Yes | High | High | Mission-critical for illumination planning but highest schema/performance/cancellation risk; follows horizon stabilization. |

## In-Tranche Sequencing Constraints

1. Horizon directory/profile contract draft and ratification (`4.5.1c`) must precede implementation work.
2. DEM-derived products can proceed in parallel once core handler contract patterns are ratified.
3. Lightmap time-series starts only after horizon artifacts and metadata conventions are stable.
4. LOS/viewshed can run in parallel with DEM-derived products after contract ratification, but uses shared CRS/tolerance policy.

## Explicit Exclusions From Tranche 1

| Capability | Why excluded now | Planned follow-up |
|---|---|---|
| Permanent shadow aggregate map (`GeneratePermanentShadowMap`) | Depends on stabilized horizon + lightmap interfaces and longer-run ephemeris policy decisions. | Candidate for Phase 4.5 tranche 2 or Phase 5.
| SPICE/station-pass analyses | Not part of immediate worker job contract surface and requires additional domain-specific contract design. | Phase 5+ analytics extension.
| Compare/debug tooling (`CompareHorizons`, emulator diagnostics scripts) | Valuable for development and regression diagnosis but not a production job-handler surface. | Keep as reference/regression-support tooling.
| Utility executables/runners (`horizon_runner`, misc scripts) | Runtime topology requires FastAPI/worker-owned contracts, not direct tool execution contracts. | Retain as reference fixtures and diagnostics.

## Lock Statement

This tranche is frozen as the baseline for `4.5.1c` contract drafting. Any scope change requires an explicit PLAN update with dependency and acceptance impact notes.
