from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from backend.services.assistant.action_router_config import default_action_router_spec_path, load_action_router_verb_aliases

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def _norm(text: str | None) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    cleaned = _TOKEN_RE.sub(" ", raw)
    return " ".join(part for part in cleaned.split() if part)


@dataclass(frozen=True)
class VerbNormalizationResult:
    canonical_operation: str | None
    normalized_input_operation: str | None
    source: str
    ambiguous: bool = False
    candidates: list[str] | None = None
    operation_candidates: list[str] | None = None
    matched_aliases_by_operation: dict[str, list[str]] | None = None

    def as_dict(self) -> dict[str, Any]:
        operation_candidates = list(self.operation_candidates or self.candidates or [])
        return {
            "canonical_operation": self.canonical_operation,
            "normalized_input_operation": self.normalized_input_operation,
            "source": self.source,
            "ambiguous": self.ambiguous,
            "candidates": list(self.candidates or []),
            "operation_candidates": operation_candidates,
            "matched_aliases_by_operation": {
                str(key): list(values)
                for key, values in dict(self.matched_aliases_by_operation or {}).items()
            },
        }


class VerbNormalizer:
    def __init__(self, *, spec_path: str | Path | None = None) -> None:
        aliases = load_action_router_verb_aliases(spec_path=Path(spec_path).resolve() if spec_path else default_action_router_spec_path())
        canonical_to_aliases: dict[str, set[str]] = {}
        alias_to_canonical: dict[str, set[str]] = {}
        for canonical, entries in aliases.items():
            key = _norm(canonical)
            if not key:
                continue
            merged = canonical_to_aliases.setdefault(key, set())
            merged.add(key)
            for raw in entries:
                norm = _norm(raw)
                if not norm:
                    continue
                merged.add(norm)
                alias_to_canonical.setdefault(norm, set()).add(key)
            alias_to_canonical.setdefault(key, set()).add(key)
        self._canonical_to_aliases = {k: sorted(v, key=len, reverse=True) for k, v in canonical_to_aliases.items()}
        self._alias_to_canonical = alias_to_canonical

    def normalize(self, *, text: str, input_operation: str | None = None) -> VerbNormalizationResult:
        op_norm = _norm(input_operation)
        input_candidates = sorted(self._alias_to_canonical.get(op_norm, set())) if op_norm else []

        text_norm = _norm(text)
        if not text_norm:
            return self._result_from_candidates(
                normalized_input_operation=op_norm or None,
                source="intent_operation" if input_candidates else ("fallback_input_operation" if op_norm else "none"),
                operation_candidates=input_candidates or ([op_norm] if op_norm else []),
                matched_aliases_by_operation={},
            )

        matched_aliases_by_operation: dict[str, set[str]] = {}
        for canonical, aliases in self._canonical_to_aliases.items():
            for alias in aliases:
                if re.search(rf"\b{re.escape(alias)}\b", text_norm):
                    matched_aliases_by_operation.setdefault(canonical, set()).add(alias)

        text_candidates = sorted(matched_aliases_by_operation.keys())
        if input_candidates:
            merged = sorted(set(text_candidates) | set(input_candidates))
            return self._result_from_candidates(
                normalized_input_operation=op_norm or None,
                source="intent_operation",
                operation_candidates=merged,
                matched_aliases_by_operation=matched_aliases_by_operation,
            )
        if text_candidates:
            return self._result_from_candidates(
                normalized_input_operation=op_norm or None,
                source="segment_text",
                operation_candidates=text_candidates,
                matched_aliases_by_operation=matched_aliases_by_operation,
            )
        return self._result_from_candidates(
            normalized_input_operation=op_norm or None,
            source="fallback_input_operation" if op_norm else "none",
            operation_candidates=[op_norm] if op_norm else [],
            matched_aliases_by_operation={},
        )

    @staticmethod
    def _result_from_candidates(
        *,
        normalized_input_operation: str | None,
        source: str,
        operation_candidates: list[str],
        matched_aliases_by_operation: dict[str, set[str]],
    ) -> VerbNormalizationResult:
        deduped = sorted(set(str(item).strip().lower() for item in operation_candidates if str(item).strip()))
        aliases = {
            str(key): sorted(set(values), key=len, reverse=True)
            for key, values in matched_aliases_by_operation.items()
            if str(key).strip()
        }
        return VerbNormalizationResult(
            canonical_operation=deduped[0] if len(deduped) == 1 else None,
            normalized_input_operation=normalized_input_operation,
            source=source,
            ambiguous=len(deduped) > 1,
            candidates=deduped,
            operation_candidates=deduped,
            matched_aliases_by_operation=aliases,
        )
