from __future__ import annotations

import inspect
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from backend.contracts.assistant_models import (
    AssistantModelMetadata,
    AssistantProviderCatalogResponse,
    AssistantProviderInfo,
)
from backend.services.assistant.providers.anthropic_provider import AnthropicProvider
from backend.services.assistant.providers.external_mcp_cli_provider import ExternalMcpCliProvider
from backend.services.assistant.providers.base import AssistantProvider, ProviderCompletion
from backend.services.assistant.providers.google_provider import GoogleProvider
from backend.services.assistant.providers.ollama_provider import OllamaProvider
from backend.services.assistant.providers.openai_provider import OpenAIProvider
from backend.services.assistant.providers.rag_wrapper_provider import RagWrapperProvider
from backend.services.assistant.providers.subprocess_provider import SubprocessProvider
from backend.services.assistant.query_router import ChannelBudget
from backend.services.assistant.rag_index import create_default_rag_index
from backend.services.assistant.rag_retriever import Fts5RagRetriever

logger = logging.getLogger(__name__)


class AssistantProviderInitializationError(RuntimeError):
    """Raised when assistant provider bootstrap fails."""


@dataclass(frozen=True)
class ProviderSelection:
    provider_id: str
    model_id: str
    execution_mode: str = "tool_loop"


@dataclass(frozen=True)
class AssistantPerformanceConfig:
    max_tool_iterations_per_turn: int = 6
    max_tool_calls_per_iteration: int = 4
    command_max_output_tokens: int = 192
    analysis_max_output_tokens: int = 1024
    empty_completion_retry_max_output_tokens: int = 4096
    first_token_timeout_ms: int = 2500
    slow_turn_fallback_provider: str | None = None
    slow_turn_fallback_model: str | None = None
    ollama_keep_alive: str | None = None
    command_provider: str | None = None
    command_model: str | None = None


class AssistantProviderRegistry:
    def __init__(
        self,
        *,
        config: dict[str, Any],
        workspace_root: str | None = None,
        model_metadata_cache_db_path: str | None = None,
    ) -> None:
        self._config = dict(config)
        self._workspace_root = str(workspace_root or "").strip() or None
        self._model_metadata_cache_db_path = str(model_metadata_cache_db_path or "").strip() or None
        self._default_provider = str(config.get("default_provider", "ollama"))
        self._default_model = str(config.get("default_model", ""))
        self._performance = _parse_performance(config.get("performance"))
        self._providers: dict[str, AssistantProvider] = {}
        self._rag_startup_refreshers: dict[str, Callable[[], dict[str, int]]] = {}
        self._init_state = "uninitialized"
        self._init_lock = threading.Lock()
        self._init_error: Exception | None = None
        self._defaults_registered = False

    def is_initialized(self) -> bool:
        return self.initialization_state() == "ready"

    def initialization_state(self) -> str:
        if self._init_state == "uninitialized" and self._providers:
            self._init_state = "ready"
            self._defaults_registered = True
        return self._init_state

    def ensure_initialized(self) -> None:
        state = self.initialization_state()
        if state == "ready":
            return
        if state == "failed" and self._init_error is not None:
            raise AssistantProviderInitializationError(
                "Assistant provider initialization failed before execution."
            ) from self._init_error
        with self._init_lock:
            state = self.initialization_state()
            if state == "ready":
                return
            if state == "failed" and self._init_error is not None:
                raise AssistantProviderInitializationError(
                    "Assistant provider initialization failed before execution."
                ) from self._init_error
            logger.info("assistant_provider_init_started")
            self._init_state = "initializing"
            try:
                self._register_defaults()
            except Exception as exc:
                self._providers.clear()
                self._rag_startup_refreshers.clear()
                self._init_error = exc
                self._init_state = "failed"
                logger.warning("assistant_provider_init_failed error=%s", exc)
                raise AssistantProviderInitializationError(
                    "Assistant provider initialization failed before execution."
                ) from exc
            self._defaults_registered = True
            self._init_error = None
            self._init_state = "ready"
            logger.info(
                "assistant_provider_init_succeeded providers=%s",
                ",".join(sorted(self._providers.keys())),
            )

    def _register_defaults(self) -> None:
        ollama_cfg = self._config.get("ollama", self._config.get("local_ollama", {}))
        if isinstance(ollama_cfg, dict) and bool(ollama_cfg.get("enabled", True)):
            model = str(ollama_cfg.get("model", self._default_model or "qwen2.5-coder:7b-instruct-q4_K_M"))
            models = _parse_models(ollama_cfg, default=[model])
            metadata_cache_db_path = _string_or_none(ollama_cfg.get("model_metadata_cache_db_path"))
            if metadata_cache_db_path is None:
                metadata_cache_db_path = self._model_metadata_cache_db_path
            global_max_context_tokens = _optional_int_or_none(self._config.get("max_context_tokens"))
            ollama_max_context_tokens = _optional_int_or_none(
                ollama_cfg.get("max_context_tokens", ollama_cfg.get("num_ctx"))
            )
            if ollama_max_context_tokens is None:
                ollama_max_context_tokens = global_max_context_tokens
            self._providers["ollama"] = OllamaProvider(
                provider_id="ollama",
                base_url=str(ollama_cfg.get("base_url", "http://127.0.0.1:11434")),
                default_model=model,
                models=models,
                keep_alive=_string_or_none(
                    ollama_cfg.get("keep_alive", self._performance.ollama_keep_alive)
                ),
                discover_models=bool(ollama_cfg.get("discover_models", True)),
                model_metadata_cache_db_path=metadata_cache_db_path,
                model_metadata_cache_ttl_seconds=_int_or_default(
                    ollama_cfg.get("model_metadata_cache_ttl_seconds"),
                    86400,
                ),
                max_context_tokens=ollama_max_context_tokens,
            )

        local_subprocess = self._config.get("local_subprocess", {})
        if isinstance(local_subprocess, dict) and bool(local_subprocess.get("enabled", False)):
            command = local_subprocess.get("command", [])
            if isinstance(command, list) and command:
                model = str(local_subprocess.get("model", self._default_model or "local-subprocess-model"))
                self._providers["local_subprocess"] = SubprocessProvider(
                    provider_id="local_subprocess",
                    command=[str(item) for item in command],
                    default_model=model,
                    models=_parse_models(local_subprocess, default=[model]),
                )
        self._register_external_cli_provider(
            provider_id="codex_cli",
            cfg=self._config.get("codex_cli", self._config.get("local_codex_cli", {})),
            default_model="gpt-5.3-codex",
            default_command=["codex", "exec"],
            default_args=["--model", "{model_id}", "--json"],
            default_mcp_only_args=["--sandbox", "read-only", "--skip-git-repo-check"],
            default_scenario_root_args=["--sandbox", "workspace-write", "--skip-git-repo-check"],
            mcp_registration_mode="codex",
            default_persistent=True,
            default_stdin_mode="turn_eof",
        )
        self._register_external_cli_provider(
            provider_id="gemini_cli",
            cfg=self._config.get("gemini_cli", self._config.get("local_gemini_cli", {})),
            default_model="gemini-2.5-pro",
            default_command=["gemini.cmd"],
            default_args=[
                "--model",
                "{model_id}",
                "--allowed-mcp-server-names",
                "{mcp_server_name}",
                "--output-format",
                "json",
            ],
            default_mcp_only_args=["--approval-mode", "plan"],
            default_scenario_root_args=[
                "--approval-mode",
                "default",
                "--include-directories",
                "{scenario_root}",
            ],
            mcp_registration_mode="gemini",
            default_persistent=True,
            default_stdin_mode="turn_eof",
        )

        remote = self._config.get("remote", {})
        if not isinstance(remote, dict):
            return
        openai_cfg = remote.get("openai", {})
        if isinstance(openai_cfg, dict) and bool(openai_cfg.get("enabled", False)):
            model = str(openai_cfg.get("model", "gpt-4.1-mini"))
            self._providers["openai"] = OpenAIProvider(
                provider_id="openai",
                api_key_env=str(openai_cfg.get("api_key_env", "OPENAI_API_KEY")),
                base_url=str(openai_cfg.get("base_url", "https://api.openai.com")),
                default_model=model,
                models=_parse_models(openai_cfg, default=[model]),
                enable_token_caching=bool(openai_cfg.get("token_caching", True)),
                prompt_cache_retention=_string_or_none(openai_cfg.get("prompt_cache_retention")),
            )
        anthropic_cfg = remote.get("anthropic", {})
        if isinstance(anthropic_cfg, dict) and bool(anthropic_cfg.get("enabled", False)):
            model = str(anthropic_cfg.get("model", "claude-3-7-sonnet-latest"))
            self._providers["anthropic"] = AnthropicProvider(
                provider_id="anthropic",
                api_key_env=str(anthropic_cfg.get("api_key_env", "ANTHROPIC_API_KEY")),
                base_url=str(anthropic_cfg.get("base_url", "https://api.anthropic.com")),
                default_model=model,
                models=_parse_models(anthropic_cfg, default=[model]),
                enable_token_caching=bool(anthropic_cfg.get("token_caching", True)),
            )
        google_cfg = remote.get("google", {})
        if isinstance(google_cfg, dict) and bool(google_cfg.get("enabled", False)):
            model = str(google_cfg.get("model", "gemini-2.0-flash"))
            self._providers["google"] = GoogleProvider(
                provider_id="google",
                api_key_env=str(google_cfg.get("api_key_env", "GOOGLE_API_KEY")),
                base_url=str(
                    google_cfg.get("base_url", "https://generativelanguage.googleapis.com")
                ),
                default_model=model,
                models=_parse_models(google_cfg, default=[model]),
                enable_token_caching=bool(google_cfg.get("token_caching", True)),
            )
        self._configure_rag_wrappers()

    def _configure_rag_wrappers(self) -> None:
        cfg = self._resolve_unified_rag_config()
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
            return

        index_rel = _string_or_none(cfg.get("global_index_relative_path")) or ".assistant/rag/global_rag.db"
        corpus_rel = _string_or_none(cfg.get("corpus_relative_root")) or "docs/rag_corpus"
        allowed_extensions = cfg.get("allowed_extensions")
        if not isinstance(allowed_extensions, list):
            allowed_extensions = ["md", "txt", "csv", "pdf", "html", "htm", "json"]
        allow_external_file_sources = bool(cfg.get("allow_external_file_sources", True))
        allow_url_fetch = bool(cfg.get("allow_url_fetch", False))
        external_source_allow_roots = cfg.get("external_source_allow_roots")
        if not isinstance(external_source_allow_roots, list):
            external_source_allow_roots = []
        rag_index = create_default_rag_index(
            db_relative_path=index_rel,
            corpus_relative_root=corpus_rel,
            allowed_extensions=[str(item) for item in allowed_extensions],
            workspace_root=self._workspace_root,
            allow_external_file_sources=allow_external_file_sources,
            allow_url_fetch=allow_url_fetch,
            external_source_allow_roots=[str(item) for item in external_source_allow_roots if str(item).strip()],
        )
        retriever = Fts5RagRetriever(index=rag_index)

        top_k = _int_or_default(cfg.get("top_k"), 6)
        max_context_chars = _int_or_default(cfg.get("max_context_chars"), 6000)
        global_max_context_tokens = _optional_int_or_none(self._config.get("max_context_tokens"))
        routing_enabled = bool(cfg.get("routing_enabled", True))
        default_channel = str(cfg.get("default_channel", "mixed")).strip().lower() or "mixed"
        max_query_terms = _int_or_default(cfg.get("max_query_terms"), 24)
        fallback_query_mode = str(cfg.get("fallback_query_mode", "and_then_or")).strip().lower() or "and_then_or"
        log_references = bool(cfg.get("log_references", False))
        budget_procedural = _parse_channel_budget(cfg.get("channel_budget_procedural"), default=ChannelBudget(0.8, 0.2))
        budget_domain = _parse_channel_budget(cfg.get("channel_budget_domain"), default=ChannelBudget(0.2, 0.8))
        budget_mixed = _parse_channel_budget(cfg.get("channel_budget_mixed"), default=ChannelBudget(0.5, 0.5))

        raw_apply = cfg.get("apply_to_providers")
        if isinstance(raw_apply, list):
            apply_to = [str(item).strip() for item in raw_apply if str(item).strip()]
        else:
            apply_to = ["ollama", "openai"]

        for provider_id in apply_to:
            base_provider = self._providers.get(provider_id)
            if base_provider is None:
                logger.warning("RAG wrapper skipped unavailable provider=%s", provider_id)
                continue
            execution_mode = str(getattr(base_provider, "execution_mode", "tool_loop"))
            if execution_mode != "tool_loop":
                logger.info("RAG wrapper skipped non-tool-loop provider=%s mode=%s", provider_id, execution_mode)
                continue
            fallback_model = _string_or_none(getattr(base_provider, "default_model", None))
            model = str(self._default_model or fallback_model or "").strip()
            if not model:
                models = base_provider.list_models()
                model = models[0] if models else "model"
            self._providers[provider_id] = RagWrapperProvider(
                provider_id=provider_id,
                base_provider=base_provider,
                retriever=retriever,
                default_model=model,
                models=[],
                top_k=top_k,
                max_context_chars=max_context_chars,
                context_window_tokens=global_max_context_tokens,
                routing_enabled=routing_enabled,
                default_channel=default_channel,
                max_query_terms=max_query_terms,
                fallback_query_mode=fallback_query_mode,
                budget_procedural=budget_procedural,
                budget_domain=budget_domain,
                budget_mixed=budget_mixed,
                log_references=log_references,
            )
            logger.info("RAG wrapper enabled provider=%s", provider_id)

        auto_refresh = bool(cfg.get("auto_refresh_on_startup", True))
        if auto_refresh:
            key = str(rag_index.db_path).lower()
            self._rag_startup_refreshers[key] = lambda: rag_index.refresh()

    def _resolve_unified_rag_config(self) -> dict[str, Any]:
        rag_cfg = self._config.get("rag")
        if isinstance(rag_cfg, dict):
            return dict(rag_cfg)
        return {}

    def _register_external_cli_provider(
        self,
        *,
        provider_id: str,
        cfg: Any,
        default_model: str,
        default_command: list[str],
        default_args: list[str],
        default_mcp_only_args: list[str],
        default_scenario_root_args: list[str],
        mcp_registration_mode: str,
        default_persistent: bool,
        default_stdin_mode: str,
    ) -> None:
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
            return
        raw_command = cfg.get("command", default_command)
        if not isinstance(raw_command, list) or not raw_command:
            return
        command = [str(item) for item in raw_command if str(item).strip()]
        if not command:
            return
        model = str(cfg.get("model", self._default_model or default_model))
        models = _parse_models(cfg, default=[model])
        args = cfg.get("args", default_args)
        if not isinstance(args, list):
            args = list(default_args)
        raw_access_mode = str(cfg.get("access_mode", "mcp_only")).strip().lower()
        access_mode = raw_access_mode if raw_access_mode in {"mcp_only", "scenario_root"} else "mcp_only"
        mcp_only_args = cfg.get("mcp_only_args", default_mcp_only_args)
        if not isinstance(mcp_only_args, list):
            mcp_only_args = list(default_mcp_only_args)
        scenario_root_args = cfg.get("scenario_root_args", default_scenario_root_args)
        if not isinstance(scenario_root_args, list):
            scenario_root_args = list(default_scenario_root_args)
        mcp_sse_url = str(cfg.get("mcp_sse_url", "http://127.0.0.1:8000/api/v1/mcp/sse")).strip()
        auth_env = _string_or_none(cfg.get("mcp_auth_token_env"))
        working_directory = _string_or_none(cfg.get("working_directory"))
        scenario_root = _string_or_none(cfg.get("scenario_root")) or self._workspace_root
        timeout_seconds = _float_or_default(cfg.get("timeout_seconds"), 180.0)
        mcp_server_name = str(cfg.get("mcp_server_name", "lunar_analyst")).strip() or "lunar_analyst"
        persistent = bool(cfg.get("persistent", default_persistent))
        raw_stdin_mode = str(cfg.get("stdin_mode", default_stdin_mode)).strip().lower()
        stdin_mode = raw_stdin_mode if raw_stdin_mode in {"stream", "turn_eof"} else default_stdin_mode
        idle_timeout_seconds = _float_or_default(cfg.get("idle_timeout_seconds"), 600.0)
        self._providers[provider_id] = ExternalMcpCliProvider(
            provider_id=provider_id,
            command=command,
            default_model=model,
            models=models,
            mcp_sse_url=mcp_sse_url,
            args=[str(item) for item in args if str(item).strip()],
            access_mode=access_mode,
            mcp_only_args=[str(item) for item in mcp_only_args if str(item).strip()],
            scenario_root_args=[str(item) for item in scenario_root_args if str(item).strip()],
            scenario_root=scenario_root,
            mcp_auth_token_env=auth_env,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            mcp_server_name=mcp_server_name,
            mcp_registration_mode=mcp_registration_mode,
            persistent=persistent,
            stdin_mode=stdin_mode,
            idle_timeout_seconds=idle_timeout_seconds,
        )

    def select(self, provider_id: str | None, model_id: str | None) -> ProviderSelection:
        self.ensure_initialized()
        provider = provider_id or self._default_provider
        if provider not in self._providers:
            if self._providers:
                provider = next(iter(self._providers.keys()))
            else:
                raise RuntimeError("No assistant provider is configured.")
        chosen_provider = self._providers[provider]
        chosen_model = model_id or self._default_model
        if not chosen_model:
            models = chosen_provider.list_models()
            chosen_model = models[0] if models else ""
        return ProviderSelection(
            provider_id=provider,
            model_id=chosen_model,
            execution_mode=str(getattr(chosen_provider, "execution_mode", "tool_loop")),
        )

    def select_for_prompt(
        self,
        *,
        provider_id: str | None,
        model_id: str | None,
        is_command_turn: bool,
    ) -> ProviderSelection:
        if is_command_turn:
            override_provider = _string_or_none(self._performance.command_provider)
            override_model = _string_or_none(self._performance.command_model)
            if provider_id is None and override_provider is not None:
                provider_id = override_provider
            if model_id is None and override_model is not None:
                model_id = override_model
        return self.select(provider_id, model_id)

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        cache_context: dict[str, str] | None = None,
        tool_schema: list[dict[str, object]] | None = None,
        max_output_tokens: int | None = None,
        thinking: bool | str | None = None,
        access_mode: str | None = None,
        scenario_working_directory: str | None = None,
    ) -> ProviderCompletion:
        self.ensure_initialized()
        provider = self._providers.get(provider_id)
        if provider is None:
            raise RuntimeError(f"Unknown assistant provider: {provider_id}")
        if isinstance(provider, ExternalMcpCliProvider):
            return _call_provider_complete(
                provider,
                {
                    "model_id": model_id,
                    "system_prompt": system_prompt,
                    "conversation": conversation,
                    "session_id": session_id,
                    "on_delta": on_delta,
                    "cache_context": cache_context,
                    "tool_schema": tool_schema,
                    "max_output_tokens": max_output_tokens,
                    "thinking": thinking,
                    "access_mode": access_mode,
                    "scenario_working_directory": scenario_working_directory,
                },
            )
        return _call_provider_complete(
            provider,
            {
                "model_id": model_id,
                "system_prompt": system_prompt,
                "conversation": conversation,
                "session_id": session_id,
                "on_delta": on_delta,
                "cache_context": cache_context,
                "tool_schema": tool_schema,
                "max_output_tokens": max_output_tokens,
                "thinking": thinking,
            },
        )

    def performance(self) -> AssistantPerformanceConfig:
        return self._performance

    def model_metadata(
        self,
        provider_id: str,
        *,
        models: list[str] | None = None,
    ) -> dict[str, AssistantModelMetadata]:
        self.ensure_initialized()
        provider = self._providers.get(provider_id)
        if provider is None:
            return {}
        list_metadata = getattr(provider, "list_model_metadata", None)
        if not callable(list_metadata):
            return {}
        try:
            raw_metadata = list_metadata(models=models)
        except Exception as exc:
            logger.warning("Failed to fetch model metadata for provider %s: %s", provider_id, exc)
            return {}
        if not isinstance(raw_metadata, dict):
            return {}
        parsed: dict[str, AssistantModelMetadata] = {}
        for model_id, item in raw_metadata.items():
            normalized_model_id = str(model_id or "").strip()
            if not normalized_model_id or not isinstance(item, dict):
                continue
            try:
                parsed[normalized_model_id] = AssistantModelMetadata.model_validate(item)
            except Exception:
                logger.warning(
                    "Ignoring invalid model metadata provider=%s model=%s payload=%s",
                    provider_id,
                    normalized_model_id,
                    item,
                )
        return parsed

    def normalize_thinking_setting(
        self,
        *,
        provider_id: str,
        model_id: str,
        thinking: bool | str | None,
    ) -> bool | str | None:
        raw = self._coerce_thinking_value(thinking)
        if raw is None:
            return None
        metadata = self.model_metadata(provider_id, models=[model_id]).get(model_id)
        mode = metadata.thinking_mode if metadata is not None else "none"
        if mode == "level":
            if isinstance(raw, bool):
                return raw
            return raw if raw in {"low", "medium", "high"} else None
        if mode == "boolean":
            if isinstance(raw, bool):
                return raw
            return raw == "true" if raw in {"true", "false"} else None
        return None

    @staticmethod
    def _coerce_thinking_value(value: bool | str | None) -> bool | str | None:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if not text:
            return None
        if text in {"true", "false", "low", "medium", "high"}:
            return text
        return None

    def catalog(self) -> AssistantProviderCatalogResponse:
        self.ensure_initialized()
        providers: list[AssistantProviderInfo] = []
        for provider_id, provider in sorted(self._providers.items()):
            models = provider.list_models()
            model_metadata = self.model_metadata(provider_id, models=models)
            kind = str(
                getattr(
                    provider,
                    "kind",
                    "remote" if provider_id in {"openai", "anthropic", "google"} else "local",
                )
            )
            execution_mode = str(getattr(provider, "execution_mode", "tool_loop"))
            providers.append(
                AssistantProviderInfo(
                    provider_id=provider_id,
                    kind=kind,
                    execution_mode=execution_mode,
                    access_mode=str(getattr(provider, "access_mode", "")) or None,
                    available=True,
                    default_model=models[0] if models else None,
                    models=models,
                    model_metadata=model_metadata,
                    notes="configured",
                )
            )
        default_provider_id: str | None = None
        default_model_id: str | None = None
        if providers:
            provider_index = {item.provider_id: item for item in providers}
            candidate_provider = str(self._default_provider or "").strip()
            if candidate_provider in provider_index:
                default_provider_id = candidate_provider
            else:
                default_provider_id = providers[0].provider_id
            chosen_provider = provider_index.get(default_provider_id)
            if chosen_provider is not None:
                candidate_model = str(self._default_model or "").strip()
                if candidate_model and candidate_model in chosen_provider.models:
                    default_model_id = candidate_model
                else:
                    default_model_id = chosen_provider.default_model
        return AssistantProviderCatalogResponse(
            default_provider_id=default_provider_id,
            default_model_id=default_model_id,
            providers=providers,
        )

    def cleanup_idle_processes(self) -> None:
        if self.initialization_state() != "ready":
            return
        for provider in self._providers.values():
            cleanup = getattr(provider, "cleanup_idle_processes", None)
            if not callable(cleanup):
                continue
            try:
                cleanup()
            except Exception as exc:
                logger.warning("Failed to cleanup idle processes: %s", exc)

    def reset_session(self, session_id: str) -> None:
        if self.initialization_state() != "ready":
            return
        target = str(session_id or "").strip()
        if not target:
            return
        for provider in self._providers.values():
            reset = getattr(provider, "reset_session", None)
            if not callable(reset):
                continue
            try:
                reset(target)
            except Exception as exc:
                logger.warning("Failed to reset provider session %s: %s", target, exc)

    def shutdown(self) -> None:
        if self.initialization_state() != "ready":
            return
        for provider in self._providers.values():
            shutdown = getattr(provider, "shutdown", None)
            if not callable(shutdown):
                continue
            try:
                shutdown()
            except Exception as exc:
                logger.warning("Failed to shutdown provider resources: %s", exc)

    def refresh_rag_indexes_on_startup(self) -> None:
        if self.initialization_state() != "ready":
            logger.info("assistant_rag_warmup_skipped reason=providers_uninitialized")
            return
        if not self._rag_startup_refreshers:
            return
        for db_path, refresh in self._rag_startup_refreshers.items():
            try:
                stats = refresh()
                logger.info(
                    "assistant rag startup refresh completed db=%s scanned=%s added=%s updated=%s skipped=%s deleted=%s",
                    db_path,
                    int(stats.get("scanned", 0)),
                    int(stats.get("added", 0)),
                    int(stats.get("updated", 0)),
                    int(stats.get("skipped", 0)),
                    int(stats.get("deleted", 0)),
                )
            except Exception as exc:
                logger.warning("assistant rag startup refresh failed db=%s error=%s", db_path, exc)


def _parse_models(cfg: dict[str, Any], *, default: list[str]) -> list[str]:
    raw = cfg.get("models")
    if isinstance(raw, list) and raw:
        models = [str(item).strip() for item in raw if str(item).strip()]
        if models:
            return models
    return list(default)


def _parse_performance(raw: Any) -> AssistantPerformanceConfig:
    if not isinstance(raw, dict):
        return AssistantPerformanceConfig()
    return AssistantPerformanceConfig(
        max_tool_iterations_per_turn=_int_or_default(raw.get("max_tool_iterations_per_turn"), 6),
        max_tool_calls_per_iteration=_int_or_default(raw.get("max_tool_calls_per_iteration"), 4),
        command_max_output_tokens=_int_or_default(raw.get("command_max_output_tokens"), 192),
        analysis_max_output_tokens=_int_or_default(raw.get("analysis_max_output_tokens"), 1024),
        empty_completion_retry_max_output_tokens=_int_or_default(
            raw.get("empty_completion_retry_max_output_tokens"), 4096
        ),
        first_token_timeout_ms=_int_or_default(raw.get("first_token_timeout_ms"), 2500),
        slow_turn_fallback_provider=_string_or_none(raw.get("slow_turn_fallback_provider")),
        slow_turn_fallback_model=_string_or_none(raw.get("slow_turn_fallback_model")),
        ollama_keep_alive=_string_or_none(raw.get("ollama_keep_alive")),
        command_provider=_string_or_none(raw.get("command_provider")),
        command_model=_string_or_none(raw.get("command_model")),
    )


def _int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _optional_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _float_or_default(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_channel_budget(value: Any, *, default: ChannelBudget) -> ChannelBudget:
    if not isinstance(value, dict):
        return default
    try:
        procedural = float(value.get("procedural", default.procedural))
        domain = float(value.get("domain", default.domain))
    except Exception:
        return default
    if procedural < 0 or domain < 0:
        return default
    total = procedural + domain
    if total <= 0:
        return default
    return ChannelBudget(procedural=procedural / total, domain=domain / total)


def _call_provider_complete(provider: AssistantProvider, kwargs: dict[str, Any]) -> ProviderCompletion:
    complete = getattr(provider, "complete")
    try:
        signature = inspect.signature(complete)
    except Exception:
        return complete(**kwargs)
    accepts_var_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )
    if accepts_var_kwargs:
        return complete(**kwargs)
    accepted = {name for name in signature.parameters}
    filtered_kwargs = {key: value for key, value in kwargs.items() if key in accepted}
    return complete(**filtered_kwargs)
