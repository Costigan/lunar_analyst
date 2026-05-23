# Phase 0.5 Step 2: Legacy Component Classification

Date: 2026-02-14
Input inventory: `docs/PHASE0_5_LEGACY_INVENTORY.md`

## Canonical Matrix Format

Use `docs/PHASE0_5_COMPONENT_CLASSIFICATION.csv` as the single source of truth for component classifications (Excel-friendly review format).

## Sync Rule

- Do not duplicate or edit the full matrix in this markdown file.
- Any classification updates must be made in `docs/PHASE0_5_COMPONENT_CLASSIFICATION.csv`.
- This markdown file should only provide context and links.

## Notes

- The CSV supersedes prior wording in this markdown file.
- `port-to-python` is intentionally selective and should be driven by explicit feature/value and regression evidence.
- Fixture reuse from legacy repos must pass Phase 0.5 fixture validation in-repo before becoming canonical regression assets.
