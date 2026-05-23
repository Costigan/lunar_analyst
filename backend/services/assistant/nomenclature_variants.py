from __future__ import annotations

from typing import Any

# Mapping of English/common terms to IAU Latin terms used in the database.
_IAU_TYPE_ALIASES: dict[str, str] = {
    "mountain": "Mons",
    "mountains": "Montes",
    "sea": "Mare",
    "plain": "Planitia",
    "plains": "Planitia",
    "valley": "Vallis",
    "ridge": "Dorsum",
    "ridges": "Dorsa",
    "marsh": "Palus",
    "lake": "Lacus",
    "ocean": "Oceanus",
    "cape": "Promontorium",
    "cliff": "Rupes",
    "cliffs": "Rupes",
    "trench": "Rima",
    "channel": "Rima",
    "crater": "Crater",
}

_KNOWN_TYPES_CASED: dict[str, str] = {
    "crater": "Crater",
    "mons": "Mons",
    "mare": "Mare",
    "massif": "Massif",
    "rima": "Rima",
    "rupes": "Rupes",
    "vallis": "Vallis",
    "dorsum": "Dorsum",
    "planitia": "Planitia",
    "palus": "Palus",
    "lacus": "Lacus",
    "promontorium": "Promontorium",
    "montes": "Montes",
    "patera": "Patera",
    "sinus": "Sinus",
    "catena": "Catena",
    "oceanus": "Oceanus",
    "dorsa": "Dorsa",
}


def _norm_whitespace(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _canonical_type(value: str | None) -> str | None:
    text = _norm_whitespace(str(value or ""))
    if not text:
        return None
    lowered = text.lower()
    if lowered in _IAU_TYPE_ALIASES:
        return _IAU_TYPE_ALIASES[lowered]
    if lowered in _KNOWN_TYPES_CASED:
        return _KNOWN_TYPES_CASED[lowered]
    return text


def _dedupe_variants(variants: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    deduped: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for raw_name, raw_type in variants:
        name = _norm_whitespace(raw_name)
        if not name:
            continue
        feature_type = _canonical_type(raw_type)
        key = (name.lower(), feature_type.lower() if feature_type else None)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((name, feature_type))
    return deduped


def _extract_embedded_type(name: str) -> tuple[str | None, str | None]:
    text = _norm_whitespace(name)
    if not text:
        return None, None
    tokens = text.split()
    if not tokens:
        return None, None

    first = _canonical_type(tokens[0])
    if first and first.lower() in _KNOWN_TYPES_CASED and len(tokens) > 1:
        return first, _norm_whitespace(" ".join(tokens[1:]))

    last = _canonical_type(tokens[-1])
    if last and last.lower() in _KNOWN_TYPES_CASED and len(tokens) > 1:
        return last, _norm_whitespace(" ".join(tokens[:-1]))

    return None, None


def get_feature_variants(name: str, feature_type: str | None) -> list[tuple[str, str | None]]:
    """
    Generate bounded exact-lookup variants for feature reference resolution.

    Strategy:
    1) Try the original text as-is.
    2) If a type context exists (or is embedded in the mention), try both prefix
       and suffix orderings.
    3) Apply simple English->IAU aliases (e.g., Mountain -> Mons).
    """
    base_name = _norm_whitespace(name)
    variants: list[tuple[str, str | None]] = []
    if not base_name:
        return variants

    explicit_type = _canonical_type(feature_type)
    variants.append((base_name, explicit_type))

    types_to_try: set[str] = set()
    if explicit_type:
        types_to_try.add(explicit_type)

    embedded_type, stripped_name = _extract_embedded_type(base_name)
    if embedded_type:
        types_to_try.add(embedded_type)
        variants.append((base_name, embedded_type))
        if stripped_name:
            variants.append((stripped_name, embedded_type))

    for type_name in list(types_to_try):
        alias = _canonical_type(type_name)
        if alias:
            types_to_try.add(alias)

    # Use stripped name when available, otherwise the original mention.
    stem = stripped_name or base_name

    for t in types_to_try:
        # Prefix variant (e.g. "Mons Malapert").
        variants.append((f"{t} {stem}", t))
        # Suffix variant (e.g. "Malapert Crater").
        variants.append((f"{stem} {t}", t))
        # Also try explicit type against original mention text.
        variants.append((base_name, t))

    # Untyped fallback should come after type-constrained variants.
    if explicit_type:
        variants.append((base_name, None))

    return _dedupe_variants(variants)


def resolve_feature_with_variants(nomenclature_service: Any, name: str, feature_type: str | None) -> dict[str, Any] | None:
    """Try exact match variations for a feature before giving up."""
    variants = get_feature_variants(name, feature_type)
    for var_name, var_type in variants:
        exact = nomenclature_service.resolve_exact(name=var_name, feature_type=var_type)
        if exact is not None:
            return exact
    return None
