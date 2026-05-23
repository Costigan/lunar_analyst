from __future__ import annotations

import json
import logging
import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable

from backend.services.assistant.providers.base import AssistantProvider, ProviderCompletion
from backend.services.assistant.query_router import ChannelBudget, route_query
from backend.services.assistant.rag_retriever import RagRetriever
from backend.services.assistant.rag_index import RetrievalBundle

logger = logging.getLogger(__name__)

_SCENARIO_ID_RE = re.compile(r"^Active scenario_id:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_VISIBILITY_INTENT_RE = re.compile(
    r"^\s*(show|hide|turn on|turn off|enable|disable)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RagWrapperProvider:
    provider_id: str
    base_provider: AssistantProvider
    retriever: RagRetriever
    default_model: str
    models: list[str]
    top_k: int = 6
    max_context_chars: int = 6000
    context_window_tokens: int | None = None
    routing_enabled: bool = True
    default_channel: str = "mixed"
    max_query_terms: int = 24
    fallback_query_mode: str = "and_then_or"
    budget_procedural: ChannelBudget = ChannelBudget(0.8, 0.2)
    budget_domain: ChannelBudget = ChannelBudget(0.2, 0.8)
    budget_mixed: ChannelBudget = ChannelBudget(0.5, 0.5)
    log_references: bool = False

    def list_models(self) -> list[str]:
        configured = [item for item in self.models if str(item).strip()]
        if configured:
            return configured
        try:
            return list(self.base_provider.list_models())
        except Exception:
            return [self.default_model]

    def list_model_metadata(self, *, models: list[str] | None = None) -> dict[str, dict[str, Any]]:
        fetch = getattr(self.base_provider, "list_model_metadata", None)
        if not callable(fetch):
            return {}
        try:
            return fetch(models=models)
        except Exception:
            logger.exception("RAG wrapper failed to fetch base provider model metadata")
            return {}

    def complete(
        self,
        *,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        cache_context: dict[str, str] | None = None,
        tool_schema: list[dict[str, object]] | None = None,
        max_output_tokens: int | None = None,
        thinking: bool | str | None = None,
    ) -> ProviderCompletion:
        user_query = _latest_user_query(conversation)
        scenario_id = _extract_scenario_id(system_prompt)
        requested_model_id = str(model_id or self.default_model).strip() or self.default_model
        effective_max_context_chars = _compute_context_char_budget(
            configured_max_context_chars=max(256, int(self.max_context_chars)),
            context_window_tokens=self.context_window_tokens,
            system_prompt=system_prompt,
            conversation=conversation,
            max_output_tokens=max_output_tokens,
        )
        bundle = RetrievalBundle(chunks=[], context_text="")
        if effective_max_context_chars > 0:
            route = route_query(
                user_query,
                default_channel=self.default_channel,
                budget_procedural=self.budget_procedural,
                budget_domain=self.budget_domain,
                budget_mixed=self.budget_mixed,
            )
            if self.routing_enabled:
                bundle = self._retrieve_routed_bundle(
                    query=user_query,
                    scenario_id=scenario_id,
                    route_intent=route.intent,
                    route_budget=route.budget,
                    max_context_chars=effective_max_context_chars,
                )
                bundle = self._apply_deterministic_guidance_triggers(
                    bundle=bundle,
                    query=user_query,
                    scenario_id=scenario_id,
                    max_context_chars=effective_max_context_chars,
                )
                logger.info(
                    "assistant rag routed retrieval provider=%s intent=%s refs=%s context_chars=%s model=%s",
                    self.provider_id,
                    route.intent,
                    len(bundle.chunks),
                    len(bundle.context_text or ""),
                    requested_model_id,
                )
            else:
                bundle = self.retriever.retrieve(
                    query=user_query,
                    top_k=max(1, int(self.top_k)),
                    max_context_chars=max(256, int(effective_max_context_chars)),
                    scenario_id=scenario_id,
                    channel=self.default_channel,
                    max_query_terms=max(4, int(self.max_query_terms)),
                    fallback_query_mode=self.fallback_query_mode,
                )
                logger.info(
                    "assistant rag single-channel retrieval provider=%s channel=%s refs=%s context_chars=%s model=%s",
                    self.provider_id,
                    self.default_channel,
                    len(bundle.chunks),
                    len(bundle.context_text or ""),
                    requested_model_id,
                )
        else:
            logger.info(
                "assistant rag context skipped provider=%s model=%s reason=context_budget_exhausted",
                self.provider_id,
                requested_model_id,
            )
        bundle = _normalize_bundle_for_injection(bundle, max_context_chars=effective_max_context_chars)
        effective_system_prompt = system_prompt
        if bundle.context_text.strip():
            effective_system_prompt = (
                f"{system_prompt}\n\n"
                "Retrieved context (trusted local corpus, cite [src#N] when used):\n"
                f"{bundle.context_text}"
            )

        completion = _call_base_provider_complete(
            self.base_provider,
            {
                "model_id": requested_model_id,
                "system_prompt": effective_system_prompt,
                "conversation": conversation,
                "session_id": session_id,
                "on_delta": on_delta,
                "cache_context": cache_context,
                "tool_schema": tool_schema,
                "max_output_tokens": max_output_tokens,
                "thinking": thinking,
            },
        )
        merged_refs: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for item in bundle.references() + list(completion.references):
            relative_path = str(item.get("relative_path", "")).strip()
            chunk_id = str(item.get("chunk_id", "")).strip()
            if not relative_path or not chunk_id:
                continue
            key = (relative_path, chunk_id)
            if key in seen:
                continue
            seen.add(key)
            merged_refs.append(dict(item))
        if self.log_references and merged_refs:
            detail_payload = [
                {
                    "relative_path": str(ref.get("relative_path", "")),
                    "chunk_id": str(ref.get("chunk_id", "")),
                    "score": float(ref.get("score", 0.0) or 0.0),
                    "title": str(ref.get("title", "")),
                    "channel": str(ref.get("channel", "")),
                }
                for ref in merged_refs
            ]
            logger.info(
                "assistant rag injected references provider=%s refs=%s detail=%s",
                self.provider_id,
                len(merged_refs),
                json.dumps(detail_payload, ensure_ascii=True, indent=2, sort_keys=True, default=str),
            )
        completion_metadata = dict(completion.metadata or {})
        if bundle.context_text.strip():
            completion_metadata["rag_context_text"] = bundle.context_text
            completion_metadata["rag_context_chars"] = len(bundle.context_text)
            completion_metadata["rag_context_reference_count"] = len(bundle.chunks)

        return ProviderCompletion(
            text=completion.text,
            tool_calls=completion.tool_calls,
            finish_reason=completion.finish_reason,
            usage=completion.usage,
            cache_attempted=completion.cache_attempted,
            cache_applied=completion.cache_applied,
            references=merged_refs,
            metadata=completion_metadata,
        )

    def _retrieve_routed_bundle(
        self,
        *,
        query: str,
        scenario_id: str | None,
        route_intent: str,
        route_budget: ChannelBudget,
        max_context_chars: int,
    ) -> RetrievalBundle:
        top_k = max(1, int(self.top_k))
        procedural_k = max(1, int(round(top_k * route_budget.procedural)))
        domain_k = max(1, int(round(top_k * route_budget.domain)))
        if procedural_k + domain_k > top_k:
            overflow = (procedural_k + domain_k) - top_k
            if route_intent == "procedural":
                domain_k = max(1, domain_k - overflow)
            elif route_intent == "domain":
                procedural_k = max(1, procedural_k - overflow)
            else:
                if procedural_k >= domain_k:
                    procedural_k = max(1, procedural_k - overflow)
                else:
                    domain_k = max(1, domain_k - overflow)
        bundles = [
            self.retriever.retrieve(
                query=query,
                top_k=procedural_k,
                max_context_chars=max(256, int(max_context_chars)),
                scenario_id=scenario_id,
                channel="procedural",
                max_query_terms=max(4, int(self.max_query_terms)),
                fallback_query_mode=self.fallback_query_mode,
            ),
            self.retriever.retrieve(
                query=query,
                top_k=domain_k,
                max_context_chars=max(256, int(max_context_chars)),
                scenario_id=scenario_id,
                channel="domain",
                max_query_terms=max(4, int(self.max_query_terms)),
                fallback_query_mode=self.fallback_query_mode,
            ),
        ]
        merged: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for bundle in bundles:
            for chunk in bundle.chunks:
                key = (str(chunk.relative_path), str(chunk.chunk_id))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(chunk)
        merged = merged[:top_k]
        context_text, injected_chunks = _render_context(merged, max_context_chars=max(256, int(max_context_chars)))
        return RetrievalBundle(chunks=injected_chunks, context_text=context_text)

    def _apply_deterministic_guidance_triggers(
        self,
        *,
        bundle: RetrievalBundle,
        query: str,
        scenario_id: str | None,
        max_context_chars: int,
    ) -> RetrievalBundle:
        text = str(query or "").strip().lower()
        if not text:
            return bundle
        if not _VISIBILITY_INTENT_RE.match(text):
            return bundle
        if any(token in text for token in ("table", "csv", "plot", "chart", "image", "file", "artifact")):
            return bundle
        targeted = self.retriever.retrieve(
            query="Layer Visibility Intent Policy Few-Shot layer.update_state show hide turn on turn off enable disable",
            top_k=1,
            max_context_chars=max(256, int(max_context_chars)),
            scenario_id=scenario_id,
            channel="procedural",
            max_query_terms=max(4, int(self.max_query_terms)),
            fallback_query_mode=self.fallback_query_mode,
        )
        if not targeted.chunks:
            return bundle
        merged: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for item in list(targeted.chunks) + list(bundle.chunks):
            key = (str(item.relative_path), str(item.chunk_id))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        merged = merged[: max(1, int(self.top_k))]
        context_text, injected_chunks = _render_context(merged, max_context_chars=max(256, int(max_context_chars)))
        return RetrievalBundle(chunks=injected_chunks, context_text=context_text)


def _latest_user_query(conversation: list[dict[str, str]]) -> str:
    for item in reversed(conversation):
        if str(item.get("role", "")).strip() == "user":
            text = str(item.get("content", "")).strip()
            if text:
                return text
    if conversation:
        return str(conversation[-1].get("content", "")).strip()
    return ""


def _extract_scenario_id(system_prompt: str) -> str | None:
    match = _SCENARIO_ID_RE.search(str(system_prompt or ""))
    if not match:
        return None
    value = str(match.group(1)).strip()
    return value or None


def _render_context(chunks: list[Any], *, max_context_chars: int) -> tuple[str, list[Any]]:
    if max_context_chars <= 0:
        return "", []
    lines: list[str] = []
    included_chunks: list[Any] = []
    used_chars = 0
    for idx, item in enumerate(chunks):
        path_text = _sanitize_tag_token(str(getattr(item, "relative_path", "") or ""))
        chunk_text = _sanitize_tag_token(str(getattr(item, "chunk_id", "") or ""))
        tag = f"[src#{idx + 1} path={path_text or 'unknown'} chunk={chunk_text or 'unknown'}]"
        content = _sanitize_context_text(str(getattr(item, "content", "") or ""))
        if not content:
            continue
        block = f"{tag}\n{content}\n"
        if used_chars + len(block) > max_context_chars:
            break
        lines.append(block)
        included_chunks.append(item)
        used_chars += len(block)
    return "\n".join(lines).strip(), included_chunks


def _normalize_bundle_for_injection(bundle: RetrievalBundle, *, max_context_chars: int) -> RetrievalBundle:
    if max_context_chars <= 0:
        return RetrievalBundle(chunks=[], context_text="")
    context_text, included = _render_context(list(bundle.chunks), max_context_chars=max_context_chars)
    return RetrievalBundle(chunks=included, context_text=context_text)


def _sanitize_tag_token(value: str) -> str:
    raw = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    raw = raw.encode("ascii", "ignore").decode("ascii")
    return " ".join(raw.split())


def _sanitize_context_text(value: str) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    raw = raw.encode("ascii", "ignore").decode("ascii")
    paragraphs: list[str] = []
    current: list[str] = []
    for line in raw.split("\n"):
        compact = " ".join(line.strip().split())
        if not compact:
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        current.append(compact)
    if current:
        paragraphs.append(" ".join(current).strip())
    return "\n\n".join(part for part in paragraphs if part)


def _compute_context_char_budget(
    *,
    configured_max_context_chars: int,
    context_window_tokens: int | None,
    system_prompt: str,
    conversation: list[dict[str, str]],
    max_output_tokens: int | None,
) -> int:
    configured = max(256, int(configured_max_context_chars))
    context_window = _positive_int_or_none(context_window_tokens)
    if context_window is None:
        return configured
    prompt_tokens = _estimate_prompt_tokens(system_prompt=system_prompt, conversation=conversation)
    if max_output_tokens is None:
        output_tokens = max(512, min(2048, context_window // 4))
    else:
        try:
            output_tokens = int(max_output_tokens)
        except Exception:
            output_tokens = 1024
        output_tokens = min(max(256, output_tokens), max(256, context_window // 2))
    available_tokens = context_window - prompt_tokens - output_tokens - 320
    if available_tokens <= 0:
        return 0
    available_chars = max(0, available_tokens * 4)
    return min(configured, available_chars)


def _estimate_prompt_tokens(*, system_prompt: str, conversation: list[dict[str, str]]) -> int:
    total_chars = len(str(system_prompt or ""))
    for item in conversation:
        if not isinstance(item, dict):
            continue
        total_chars += len(str(item.get("role", "") or "")) + 4
        total_chars += len(str(item.get("content", "") or "")) + 8
    if total_chars <= 0:
        return 0
    return max(1, (total_chars + 3) // 4)


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _call_base_provider_complete(provider: AssistantProvider, kwargs: dict[str, Any]) -> ProviderCompletion:
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
