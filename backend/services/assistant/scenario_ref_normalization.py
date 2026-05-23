from __future__ import annotations

import re

_LEADING_FILLER_RE = re.compile(r"^(?:to|the|scenario|scenario:)\s+", re.IGNORECASE)
_TRAILING_PUNCT_RE = re.compile(r"[.?!;:,]+$")


def normalize_scenario_reference(raw: str) -> str:
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        return ""
    text = _TRAILING_PUNCT_RE.sub("", text).strip()
    while True:
        next_text = _LEADING_FILLER_RE.sub("", text).strip()
        if next_text == text:
            break
        text = next_text
    lowered = text.lower()
    if lowered.endswith(" scenario"):
        text = text[: -len(" scenario")].strip()
    text = _TRAILING_PUNCT_RE.sub("", text).strip()
    return text


def canonicalize_scenario_reference(raw: str) -> str:
    text = normalize_scenario_reference(raw).lower()
    if not text:
        return ""
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
