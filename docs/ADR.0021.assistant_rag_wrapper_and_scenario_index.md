# ADR 0021: Assistant RAG Wrapper Providers and Workspace-Global Retrieval Index

- Status: Accepted
- Date: 2026-03-10
- Owners: Architecture (Codex), Implementation (Gemini)
- Related: `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0019.unified_tool_model.md`, `docs/ADR.0002.scenario_filesystem_and_catalog.md`, `docs/DESIGN.md`, `AGENTS.md`

## Context

Lunar Analyst supports multiple assistant providers through a shared provider registry and tool loop. We need Retrieval-Augmented Generation (RAG) for both:
- local `gpt-oss:20b` via Ollama
- remote GPT-5 family via OpenAI

Constraints:
- Existing providers must not regress or be replaced.
- RAG should appear as additional provider options in the same provider/model picker.
- Filesystem safety remains mandatory (no out-of-root traversal).
- Confirmation policy, session persistence, assistant WS contracts, and rich output contracts must remain compatible.
- Startup readiness must remain non-blocking.
- JobHandlers-centered compute invariant must hold for long-running ingestion/index operations.

## Decision

1. Introduce a generic `RagWrapperProvider` that decorates an existing base provider and preserves the existing provider completion contract.
2. Register two additive providers:
- `rag_ollama` (base: `ollama`)
- `rag_openai` (base: `openai`)
3. Keep existing `ollama` and `openai` providers unchanged and selectable.
4. Keep source RAG documents in a git-managed project directory:
- `<repo_root>/docs/rag_corpus/`
5. Use one workspace-global RAG database for all scenarios:
- `<workspace_root>/.assistant/rag/global_rag.db`
6. Retrieval engine architecture:
- Required engine: SQLite FTS5 lexical retrieval.
- Vector retrieval is not required in the first slice.
- If vector retrieval is added later, it must be a pluggable retriever implementation behind the same retrieval interface.
7. Preserve structured citation reliability without API schema break:
- Add internal provider-returned source reference metadata.
- Persist references into existing assistant message `metadata` (`source_references`).
- Do not change assistant message/WS schema shapes.
8. Implement ingestion/index update as typed handler-backed jobs in `ToolImplementations`.
9. Add startup auto-refresh as background incremental sync using timestamp-based diff plus optional hash verification.

## Rationale

- Wrapper providers minimize risk and preserve current non-RAG behavior.
- Separate provider IDs allow safe rollout, A/B testing, and quick fallback.
- A workspace-global index supports cross-scenario retrieval and avoids redundant indexes.
- Git-managed corpus keeps retrieval sources versioned, reviewable, and reproducible with code changes.
- Retrieval engine abstraction avoids feature-flag complexity leaking into provider logic.
- Structured source references in metadata are deterministic for UI rendering and auditability.

## Consequences

Positive:
- Dual RAG support for both target model paths (`gpt-oss:20b` and GPT-5).
- Existing providers remain stable and available as non-RAG fallback.
- Cross-scenario retrieval is possible from a single index.
- Citation rendering can be reliable without changing external schemas.

Tradeoffs:
- Global DB requires stronger scenario/root scoping discipline.
- Index update logic must handle scenario add/remove lifecycle.
- Slight latency/token overhead in RAG-enabled turns.
- Central corpus may require periodic curation to avoid stale/general docs affecting domain-specific turns.

## Non-Goals

- Replacing existing providers (`ollama`, `openai`, external CLI adapters).
- Introducing a required external vector DB service.
- Changing assistant WS schema/version.
- Auto-executing file-path-based tools from model-emitted citation text.
- Blocking startup on full indexing.

## Data and Security Model

### Global Index Layout

- Source corpus root (git-managed):
- `<repo_root>/docs/rag_corpus/`
- DB path: `<workspace_root>/.assistant/rag/global_rag.db`
- Required tables:
- `documents(doc_id, scenario_id, scenario_root, relative_path, mtime_utc, size_bytes, sha256, chunking_mode, updated_at_utc)`
- `chunks(chunk_id, doc_id, ordinal, content_text, token_estimate, metadata_json)`
- FTS5 table for lexical search over chunk text.

### Path Validation Rules

- Ingest roots are allowlisted and default to:
- `<repo_root>/docs/rag_corpus/`
- Persist only canonical `scenario_id + relative_path` identities.
- Resolve absolute paths only from allowlisted roots (`docs/rag_corpus` in this phase).
- Reject absolute user paths, drive-letter escapes, `..`, symlink escapes.
- Validate every reference before prompt injection and before any tool argument handoff.

### Prompt Injection and Path Hallucination Controls

- Citation references injected into prompts are generated from sanitized DB rows only.
- Model-emitted file paths are never trusted as-is.
- Any follow-up tool call involving paths must pass existing scenario-root allowlist checks.

## Chunking Policy

### Default Chunking

- `.md`: header-aware and table-aware chunking.
- `.txt`: paragraph-aware chunking.
- `.csv`: row-group chunking that preserves row integrity and includes header row context.

### Single-Chunk Directive for `.md` and `.txt`

- If line 1 is exactly `RAG_CHUNKING: single`, ingest the entire file as one chunk.
- Directive line is metadata and is not included in chunk content.
- Any other value on line 1 is ignored for chunking control in this phase.

## Citation Metadata Contract

- Provider returns structured references with each completion (internal contract).
- Assistant service writes references to existing assistant message `metadata`:
- `metadata.source_references = [{ "scenario_id": "...", "relative_path": "...", "chunk_id": "...", "score": 0.0, "snippet": "..." }]`
- This is additive and uses existing free-form metadata fields; no API model schema change required.

## Detailed Implementation Plan

### Phase 0: Contract and Config Specification

Files:
- `backend/services/assistant/provider_registry.py`
- `backend/contracts/assistant_models.py` (only if optional internal metadata typing helpers are added; no wire schema change)
- `config/lunar_analyst.toml`
- `docs/PLAN.md`

Work:
- Add config blocks:
- `[backend.llm.rag_ollama]`
- `[backend.llm.rag_openai]`
- Shared fields:
- `enabled`, `base_provider`, `model`, `models`, `top_k`, `max_context_chars`, `allowed_extensions`, `auto_refresh_on_startup`
- Global index field:
- `global_index_relative_path = ".assistant/rag/global_rag.db"`
- Corpus root field:
- `corpus_relative_root = "docs/rag_corpus"`
- Retrieval engine field:
- `retriever = "fts5"` (future values allowed, e.g., `hybrid`)

Exit criteria:
- Approved config and no external schema change decision documented.

### Phase 1: Wrapper Provider and Structured References

Files:
- Add `backend/services/assistant/providers/rag_wrapper_provider.py`
- Change `backend/services/assistant/provider_registry.py`
- Change `backend/services/assistant/providers/base.py` (add optional internal `references` payload to `ProviderCompletion`)
- Change `backend/services/assistant/assistant_service.py` (persist references into message metadata)
- Add `backend/tests/worker/test_rag_wrapper_provider.py`
- Update `backend/tests/worker/test_assistant_provider_tool_contract.py`

Work:
- Implement `RagWrapperProvider.complete(...)`:
- Retrieve context from retriever service.
- Inject bounded context plus normalized citation tags.
- Delegate to base provider with unchanged tool args and limits.
- Return `ProviderCompletion` with optional structured `references`.
- In assistant service, copy references into `AssistantMessage.metadata.source_references`.

Exit criteria:
- Provider catalog lists `rag_ollama` and `rag_openai` when enabled.
- Existing non-RAG provider behavior unchanged.
- Message metadata carries structured source references.

### Phase 2: Global Index + Retriever Interface

Files:
- Add `backend/services/assistant/rag_index.py`
- Add `backend/services/assistant/rag_retriever.py`
- Add `backend/tests/worker/test_rag_index_path_safety.py`
- Add `backend/tests/worker/test_rag_retrieval_fts5.py`

Work:
- Implement index I/O service over `global_rag.db`.
- Implement retriever interface:
- `class RagRetriever(Protocol): retrieve(query, scenario_scope, top_k, budget) -> list[RetrievedChunk]`
- Implement FTS5 retriever as default concrete class.
- Keep wrapper unaware of FTS/vector specifics.
- Enforce reference/path sanitization before returning retrieved chunks.

Exit criteria:
- Deterministic lexical retrieval tests pass.
- Path safety and sanitization tests pass.

### Phase 3: Ingestion as Typed Job + Tooling

Files:
- Change `backend/jobs/handlers.py`
- Change `backend/services/assistant/tool_registry.py`
- Add `backend/tests/worker/test_rag_ingest_handler.py`
- Update `backend/tests/worker/test_assistant_tool_loop.py`

Work:
- Add `ToolImplementations.assistant_rag_ingest(...)`:
- Inputs: `scenario_id`, `relative_root`, `rebuild`, `extensions`, `respect_directives`.
- Inputs: `scenario_id` (optional scope hint), `relative_root` (defaults to `docs/rag_corpus`), `rebuild`, `extensions`, `respect_directives`.
- Apply chunking policy including `RAG_CHUNKING: single` directive.
- Emit progress and support cancellation.
- Add assistant tool `scenario.rag_ingest` mapped to `LAUNCH_JOB` confirmation class.

Exit criteria:
- Ingest job supports full refresh and incremental refresh.
- Directive-controlled single-chunk behavior validated in tests.

### Phase 4: Startup Incremental Auto-Refresh

Files:
- Change `backend/api/dependencies.py`
- Add `backend/tests/worker/test_rag_startup_refresh.py`

Work:
- Background startup task:
- Enumerate scenario docs roots.
- Compare index metadata (`mtime` + `size`), compute `sha256` only on changed candidates.
- Queue incremental ingest/update/delete operations.
- Log cycle stats and duration.

Exit criteria:
- Backend readiness is not blocked by refresh.
- Updated docs become retrievable after startup refresh without manual full rebuild.

### Phase 5: Contract, Integration, and Regression Validation

Files:
- Add `backend/tests/contract/test_assistant_provider_catalog_rag.py`
- Update `backend/tests/contract/test_phase6_assistant_api.py`
- Update `backend/tests/contract/test_phase6_assistant_ws.py`
- Optional UI test updates where metadata citations are rendered.

Work:
- Validate no API/WS breaking change.
- Validate catalog entries and provider selection flow.
- Validate metadata citation payload presence and shape.
- Validate no regressions in existing provider/tool loop paths.

Exit criteria:
- Contract suite green.
- Existing assistant behavior remains stable when RAG providers disabled.

## Observability Plan

Structured logs:
- retrieval latency, hits, query budget, context bytes injected
- source reference count and sanitization drops
- ingest counts (scanned/added/updated/deleted/skipped), cancellation status, duration
- startup refresh run metrics per scenario and total

## Risk Controls

1. Citation reliability risk:
- Use structured metadata references, not prose-only citations.

2. Chunk quality risk:
- Enforce type-aware chunking and single-chunk directive support.

3. Path security risk:
- Sanitize all references before prompt injection and before tool execution.

4. Future vector complexity risk:
- Keep a strict retriever interface boundary; no wrapper-level vector feature flags.

## Rollback Plan

1. Disable RAG wrappers:
- `[backend.llm.rag_ollama].enabled = false`
- `[backend.llm.rag_openai].enabled = false`
2. Restart backend.
3. Preserve or remove global index as needed:
- `<workspace_root>/.assistant/rag/global_rag.db`
4. Revert additive files and registry wiring if code rollback is required.
