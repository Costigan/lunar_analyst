# List of Feature Toggles

This document catalogs all configuration toggles that gate code paths in Lunar Analyst.

---

## `[backend.llm]` section

| Toggle | Description |
|--------|-------------|
| `enabled` | Master enable for the assistant |
| `hybrid_command_router_enabled` | Enables the hybrid command router (deterministic + model) vs. legacy path |
| `legacy_parser_enabled` | Fallback to pre-ADR.0053 parser (disabled by default) |
| `deterministic_agent_substeps_enabled` | Allows `agent_call` steps in deterministic action plans |
| `create_product_recipe_catalog_enabled` | Enables recipe-catalog execution for product creation |
| `prompt_segmentation_model` | spaCy model for sentence boundary detection |
| `require_confirmation_for_mutations` | Forces confirmation UI for state-changing tools |
| `session_store_backend` | SQLite or legacy JSON storage for sessions |

---

## `[backend.llm.routing]` section (ADR.0053)

| Toggle | Description |
|--------|-------------|
| `entity_kind_routing_enabled` | Entity-kind-aware typed deterministic routing |
| `domain_entity_context_enabled` | `<DOMAIN_ENTITY_CONTEXT>` injection to primary LLM |
| `semantic_classifier_fallback_enabled` | Semantic classifier invoked only when deterministic fails |

---

## `[backend.llm.performance]` section

| Toggle | Description |
|--------|-------------|
| `allow_cross_provider_fallback` | Allow fallback to different providers on failure |
| `prewarm_on_startup` | Warm providers at backend startup |

---

## Provider-specific toggles

### `[backend.llm.ollama]`

| Toggle | Description |
|--------|-------------|
| `enabled` | Enable Ollama provider |
| `discover_models` | Auto-discover models from `/api/tags` |

### `[backend.llm.local_subprocess]`

| Toggle | Description |
|--------|-------------|
| `enabled` | Enable local subprocess LLM worker |

### `[backend.llm.remote.openai/anthropic/google]`

| Toggle | Description |
|--------|-------------|
| `enabled` | Enable each remote provider |

### `[backend.llm.codex_cli/gemini_cli]`

| Toggle | Description |
|--------|-------------|
| `enabled` | Enable external CLI agent adapters |
| `persistent` | Keep CLI process alive across turns |
| `access_mode` | `mcp_only` vs. `scenario_root` per-turn toggle |

---

## `[backend.llm.rag]`

| Toggle | Description |
|--------|-------------|
| `enabled` | Enable RAG wrapper |
| `routing_enabled` | Channel-aware retrieval routing |
| `allow_external_file_sources` | Allow `source_kind: file` in corpus |
| `allow_url_fetch` | Allow `source_kind: url` in corpus |
| `auto_refresh_on_startup` | Rebuild RAG index at startup |

---

## `[backend.mcp]`

| Toggle | Description |
|--------|-------------|
| `enabled` | Master MCP enable |
| `http_enabled` | HTTP transport |
| `stdio_enabled` | stdio transport |
| `sse_enabled` | SSE transport |

---

## Other notable toggles

| Toggle | Location | Description |
|--------|----------|-------------|
| `backend.viewshed.backend_mode` | `[backend.viewshed]` | `gdal` vs `cuda` vs `auto` for viewshed |
| `backend.marimo.auto_start` | `[backend.marimo]` | Auto-launch Marimo at startup |
| `backend.scenario_discovery.auto_discover_on_startup` | `[backend.scenario_discovery]` | Discover scenarios on startup |
| `backend.scenario_discovery.reconcile_missing_on_startup` | `[backend.scenario_discovery]` | Remove missing scenario entries |

---

**Total: ~30+ toggles across multiple config sections. The assistant alone has ~15 flags.**