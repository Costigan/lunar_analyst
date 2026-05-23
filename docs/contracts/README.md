# Contract Artifacts: Canonical Locations

## Canonical Published Artifacts

- OpenAPI (v1): `docs/contracts/generated/v1/openapi.json`
- JSON Schemas (v1): `docs/contracts/generated/v1/*.schema.json`
- WS envelope schema (v1): `docs/contracts/generated/v1/ws_event_envelope.schema.json`

These files are generated and are the canonical published contract artifacts consumed by tests and tooling.

## Authoritative Code Sources

- REST app/router signatures: `backend/api/app.py`, `backend/api/routers/v1.py`, `backend/api/job_runtime.py`
- Shared contract models: `backend/contracts/models.py`, `backend/contracts/events.py`, `backend/contracts/types.py`
- Signature-first job handler contracts: `backend/jobs/handlers.py`

Generated artifacts must be derived from these sources only:
- `python -m backend.tools.export_openapi`
- `python -m backend.tools.export_contract_schemas`

## Historical Snapshots (Non-Canonical)

- `docs/contracts/openapi.v1.stage1.yaml`
- `docs/contracts/schemas/v1/*.schema.json`

These are retained as historical Stage 1 snapshots and are not the canonical contract outputs going forward.

## Change Control

Any contract change requires all of:
- Compatibility classification: `additive` or `breaking`
- Regenerated artifacts under `docs/contracts/generated/v1/`
- Changelog entry in `docs/contracts/CHANGELOG.md`
- Updated contract tests

