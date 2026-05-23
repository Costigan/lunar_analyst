from __future__ import annotations

import math
from typing import Any

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError

_PROJECTED_SAMPLE_POINTS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (1.0, 0.0),
    (0.0, 1.0),
    (1_000.0, 2_000.0),
    (-1_000.0, 2_000.0),
    (100_000.0, -100_000.0),
)

_GEOGRAPHIC_SAMPLE_POINTS: tuple[tuple[float, float], ...] = (
    (0.0, -80.0),
    (45.0, -85.0),
    (-120.0, -70.0),
    (170.0, -60.0),
)

_STEREOGRAPHIC_METHOD_TOKENS: tuple[str, ...] = (
    "polar stereographic",
    "oblique stereographic",
)


def _to_crs(value: Any) -> CRS | None:
    if value is None:
        return None
    try:
        return CRS.from_user_input(value)
    except (CRSError, ProjError, TypeError, ValueError):
        return None


def _max_identity_delta(transformer: Transformer, points: tuple[tuple[float, float], ...]) -> float:
    max_delta = 0.0
    for x, y in points:
        tx, ty = transformer.transform(x, y)
        if not math.isfinite(tx) or not math.isfinite(ty):
            return math.inf
        max_delta = max(max_delta, abs(tx - x), abs(ty - y))
    return max_delta


def _stereographic_fallback_allowed(crs: CRS) -> bool:
    if not crs.is_projected:
        return False
    op = crs.coordinate_operation
    method_name = (getattr(op, "method_name", None) or "").strip().lower()
    if method_name:
        return any(token in method_name for token in _STEREOGRAPHIC_METHOD_TOKENS)
    try:
        proj4 = crs.to_proj4().lower()
    except Exception:
        return False
    return "+proj=stere" in proj4


def crs_semantically_equivalent(
    left: Any,
    right: Any,
    *,
    projected_tolerance: float = 1e-4,
    geographic_tolerance: float = 1e-9,
) -> bool:
    left_crs = _to_crs(left)
    right_crs = _to_crs(right)
    if left_crs is None or right_crs is None:
        return False

    if left_crs.equals(right_crs, ignore_axis_order=True):
        return True

    if not (_stereographic_fallback_allowed(left_crs) and _stereographic_fallback_allowed(right_crs)):
        return False

    sample_points = (
        _PROJECTED_SAMPLE_POINTS
        if left_crs.is_projected and right_crs.is_projected
        else _GEOGRAPHIC_SAMPLE_POINTS
    )
    tolerance = (
        projected_tolerance
        if left_crs.is_projected and right_crs.is_projected
        else geographic_tolerance
    )
    try:
        forward = Transformer.from_crs(left_crs, right_crs, always_xy=True)
        reverse = Transformer.from_crs(right_crs, left_crs, always_xy=True)
    except (CRSError, ProjError):
        return False

    forward_delta = _max_identity_delta(forward, sample_points)
    reverse_delta = _max_identity_delta(reverse, sample_points)
    return forward_delta <= tolerance and reverse_delta <= tolerance
