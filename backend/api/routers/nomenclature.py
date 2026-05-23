from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import ServiceContainer, get_services
from backend.services.nomenclature_service import NomenclatureService

router = APIRouter(prefix="/api/v1/nomenclature", tags=["nomenclature"])


def _service(services: ServiceContainer) -> NomenclatureService:
    return NomenclatureService(db_path=services.stores.catalog_db_path)


@router.get("/search")
def search_nomenclature(
    query: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=200),
    type: str | None = Query(default=None),
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    svc = _service(services)
    items = svc.search_fuzzy(query=query, limit=limit, feature_type=type)
    return {"query": query, "count": len(items), "items": items}


@router.get("/resolve")
def resolve_nomenclature(
    name: str = Query(min_length=1),
    type: str | None = Query(default=None),
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    svc = _service(services)
    item = svc.resolve_exact(name=name, feature_type=type)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "nomenclature_not_found",
                "message": "No nomenclature feature matched the requested exact name.",
                "details": {"name": name, "type": type},
            },
        )
    return item


@router.get("/nearby")
def nearby_nomenclature(
    x: float = Query(),
    y: float = Query(),
    limit: int = Query(default=25, ge=1, le=500),
    type: str | None = Query(default=None),
    radius_m: float | None = Query(default=None, gt=0),
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    svc = _service(services)
    items = svc.nearby(x=x, y=y, limit=limit, feature_type=type, radius_m=radius_m)
    return {
        "query_point": {"x": x, "y": y, "crs": "ESRI:103878"},
        "type": type,
        "radius_m": radius_m,
        "count": len(items),
        "items": items,
    }


@router.get("/features")
def extent_nomenclature(
    extent: str = Query(description="min_x,min_y,max_x,max_y in ESRI:103878"),
    types: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    parts = [p.strip() for p in str(extent).split(",") if p.strip()]
    if len(parts) != 4:
        raise HTTPException(status_code=422, detail="extent must contain 4 comma-separated numbers")
    try:
        bbox = [float(p) for p in parts]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="extent must contain numeric values") from exc
    parsed_types = [p.strip() for p in str(types or "").split(",") if p.strip()]
    svc = _service(services)
    items = svc.get_features_in_extent(extent=bbox, types=parsed_types, limit=limit)
    return {
        "extent": bbox,
        "types": parsed_types,
        "count": len(items),
        "items": items,
    }
