# RAG Upgrade Plan: Routed Multi-Channel Retrieval + Front-Matter Corpus

## 1. Goal
Upgrade the current lexical-only, single-channel RAG path to support:
- hand-editable metadata-driven documents;
- query routing across distinct retrieval channels (`procedural`, `domain`, `mixed`);
- safer, higher-recall retrieval behavior for long/complex queries;
- large-document ingest/chunking (for example, *The Lunar Source Book*);
- additive rollout with existing RAG providers preserved.

## 2. Scope and Non-Goals
In scope:
- New front-matter metadata format for `.md`/`.txt` corpus docs.
- Channel-aware indexing and retrieval filtering.
- Query router and retrieval fan-out/fusion for RAG wrapper providers.
- Improved lexical query strategy (replace fixed first-8 token hard limit).
- External-source descriptors (`source_kind=file|url`) with controlled ingest behavior.
- External source parsing for `.pdf`, `.html/.htm`, and `.json` (no `.docx` in this plan).
- Tests, observability, and rollback controls.

Out of scope (this plan):
- Replacing existing non-RAG providers.
- Breaking assistant API/WS schemas.
- Mandatory vector DB dependency in phase 1.
- Auto-executing model-emitted file paths.

## 3. Design Summary
### 3.1 Document Format (Hand-Editable)
Adopt lightweight front matter in `.md`/`.txt` where top lines are `key: value`.
Parsing rule:
1. Parse consecutive top lines that match `^[a-zA-Z_][a-zA-Z0-9_\-]*:\s+.*$`.
2. Stop at first line that does not match.
3. Remaining lines are body content.
4. Unknown keys are preserved in metadata JSON.

Required keys:
- `title`
- `channel` (`procedural|domain|mixed`)

Optional keys:
- `doc_type`, `tags`, `authority`, `source_kind`, `source_ref`, `chunking`, `chunk_size_chars`, `chunk_overlap_chars`, `language`, `updated_at`

Back-compat:
- Files without front matter are ingested with inferred defaults:
  - `title = <relative_path>`
  - `channel = mixed`

### 3.2 Index Model Extensions
Extend RAG DB schema with additive metadata columns/tables:
- `documents`: `title`, `channel`, `source_kind`, `source_ref`, `tags_json`, `authority`, `metadata_json`.
- `chunks`: `section`, `token_estimate`, optional `keywords_json`.
- Add indexes on `documents.channel`, `documents.title`.

### 3.3 Retrieval Architecture
Introduce channel-routed retrieval in wrapper path:
- Router labels query intent: `procedural`, `domain`, or `mixed`.
- Retrieval fan-out:
  - `procedural`: prioritize procedural channel.
  - `domain`: prioritize domain channel.
  - `mixed`: balanced pull across both.
- Fusion: deterministic merge with stable ordering and source tags.

Initial retriever remains FTS5 lexical; keep retriever interface boundary for future hybrid/vector addition.

### 3.4 Query Strategy Upgrade
Replace fixed first-8-token rule with configurable strategy:
- normalize + tokenize;
- stopword filtering + minimum token length;
- cap by configurable `max_query_terms` (default 24);
- fallback plan when strict `AND` has low/no hits:
  1. strict `AND`;
  2. softened query (`OR`/reduced constraints) with precision guardrails.

### 3.5 Large Document Support
Support large references via metadata controls:
- `chunking: section|paragraph|sliding_window|single`
- `chunk_size_chars`, `chunk_overlap_chars`
- optional `source_kind=file|url`

Phase behavior:
- `source_kind=inline` (default): body text in file.
- `source_kind=file`: ingest external file content only when path passes allowlist/safety checks.
- `source_kind=url`: fetch only static documents (no JS rendering) and parse as text source when enabled.

Supported external formats in this plan:
- File-based: `.pdf`, `.html`, `.htm`, `.json`, `.md`, `.txt`, `.csv`
- URL-based: static `.pdf` URLs and static HTML pages (`text/html`); optional plain text/markdown endpoints
- Explicitly excluded: `.docx`, JS-heavy/dynamic pages, authenticated crawling, browser automation

## 4. Delivery Phases (Small, Testable Slices)

### Phase A: Front-Matter Parsing + Schema Additions
Files:
- `backend/services/assistant/rag_index.py`
- `backend/tests/worker/test_rag_front_matter.py` (new)
- `docs/rag_corpus/README.md`

Tasks:
- Add parser for top-of-file `key: value` metadata.
- Persist parsed metadata in DB (additive schema migration-in-code pattern).
- Keep no-front-matter files working with defaults.

Acceptance criteria:
- Front-matter metadata appears in indexed document rows.
- Legacy corpus files still ingest and retrieve.
- No API/WS schema changes.

### Phase B: Channel-Aware Ingest and Retrieval Filters
Files:
- `backend/services/assistant/rag_index.py`
- `backend/services/assistant/rag_retriever.py`
- `backend/tests/worker/test_rag_retrieval_channels.py` (new)

Tasks:
- Add `channel` filter support in retrieval API.
- Keep default behavior (`mixed`) when channel not specified.
- Include channel in reference metadata for debugging/observability.

Acceptance criteria:
- Channel-filtered retrieval deterministically returns only expected channel docs.
- Existing default retrieval behavior remains compatible.

### Phase C: Query Router + Fan-Out/Fusion in Wrapper
Files:
- `backend/services/assistant/providers/rag_wrapper_provider.py`
- `backend/services/assistant/query_router.py` (new)
- `backend/tests/worker/test_rag_wrapper_routing.py` (new)

Tasks:
- Implement simple deterministic intent router.
- Allocate retrieval budgets by route (`procedural`, `domain`, `mixed`).
- Merge/fuse results with dedupe and stable source tagging.

Acceptance criteria:
- Procedural and domain prompts show channel-appropriate references.
- Mixed prompts include both channel types when available.
- No regressions in tool loop behavior.

### Phase D: Query-Term Strategy Upgrade
Files:
- `backend/services/assistant/rag_index.py`
- `config/lunar_analyst.toml`
- `backend/tests/worker/test_rag_query_strategy.py` (new)

Tasks:
- Add configurable token cap and fallback strategy.
- Remove hard-coded first-8-token truncation behavior.

Acceptance criteria:
- Long queries retrieve relevant chunks beyond first 8 terms.
- Precision is preserved with fallback guardrails.

### Phase E: Large-Doc Chunking Controls + External Source Descriptors
Files:
- `backend/services/assistant/rag_index.py`
- `backend/jobs/handlers.py`
- `backend/services/assistant/tool_registry.py`
- `backend/tests/worker/test_rag_large_doc_chunking.py` (new)
- `backend/tests/worker/test_rag_external_source_safety.py` (new)
- `backend/tests/worker/test_rag_external_format_parsers.py` (new)

Tasks:
- Honor per-document chunking hints from metadata.
- Support `source_kind=file` with strict path allowlist and normalization.
- Add parsers for external `.pdf`, `.html/.htm`, and `.json` ingestion.
- For `source_kind=url`, support static document fetch only (PDF/HTML/text); reject JS-render-required flows.

Acceptance criteria:
- Large source docs chunk according to per-doc policy.
- Unsafe external paths are rejected.
- `.pdf`, `.html/.htm`, and `.json` external sources ingest to retrievable chunks.
- URL ingestion handles static HTML/PDF and rejects disallowed/dynamic sources with clear errors.
- Ingest remains cancellable with progress reporting.

### Phase F: Observability, Docs, and Rollout Controls
Files:
- `backend/services/assistant/*`
- `docs/DESIGN.md`
- `docs/ADR.0021.assistant_rag_wrapper_and_scenario_index.md` (status/update notes)
- `docs/HOW_TO_MANUALLY_TEST.md`

Tasks:
- Log route decision, query plan, hit counts per channel, fusion drops, context bytes.
- Document front-matter format and retrieval behavior.
- Add config flags for staged rollout and quick disable.

Acceptance criteria:
- Manual test checklist covers procedural/domain/mixed scenarios.
- RAG wrappers can be disabled with no regressions.

## 5. Proposed Config Additions
Under existing RAG provider blocks:
- `routing_enabled = true`
- `default_channel = "mixed"`
- `max_query_terms = 24`
- `fallback_query_mode = "and_then_or"`
- `channel_budget_procedural = { procedural = 0.8, domain = 0.2 }`
- `channel_budget_domain = { procedural = 0.2, domain = 0.8 }`
- `channel_budget_mixed = { procedural = 0.5, domain = 0.5 }`

Global/ingest:
- `allow_external_file_sources = true`
- `allow_url_fetch = false` (default)
- `external_source_allow_roots = ["docs/rag_corpus", "<optional curated roots>"]`

## 6. Testing Plan
Unit:
- front-matter parser success/failure and unknown-key preservation;
- query strategy (long query, stopwords, fallback);
- router classification and budget assignment;
- chunking policy by `chunking` metadata.
- format parser behavior for `.pdf`, `.html/.htm`, `.json` (including malformed input handling).

Worker/integration:
- ingest refresh with mixed corpus types;
- channel-filtered retrieval determinism;
- wrapper provider context injection references + dedupe;
- external file source safety checks.
- static URL fetch and parse for PDF/HTML with content-type and size guards.

Contract regression:
- provider catalog unchanged except additive options;
- assistant API/WS payload shapes unchanged;
- non-RAG providers unaffected.

## 7. Risks and Mitigations
Risk: routing mistakes hurt relevance.
- Mitigation: deterministic router first + telemetry + easy disable.

Risk: metadata drift in hand-authored docs.
- Mitigation: strict validation warnings + defaults + lint tool (future).

Risk: external file ingest expands attack surface.
- Mitigation: allowlist roots, canonical path checks, explicit feature flag.

Risk: larger context increases token/latency costs.
- Mitigation: hard context char budget + per-route caps.

## 8. Rollback Plan
1. Disable routed behavior via config (`routing_enabled = false`).
2. Disable RAG wrapper providers (`rag_ollama.enabled=false`, `rag_openai.enabled=false`) if needed.
3. Keep or remove `.assistant/rag/global_rag.db` based on incident response needs.
4. Revert additive parser/router files if code rollback is required.

## 9. Suggested Execution Order
1. Phase A (front matter + schema)
2. Phase B (channel filters)
3. Phase D (query strategy upgrade)
4. Phase C (router/fan-out)
5. Phase E (large-doc + external descriptors)
6. Phase F (docs + observability + rollout hardening)

This order preserves backward compatibility while improving retrieval quality early.
