from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from typing import Any, Callable, get_type_hints

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from backend.api.dependencies import ServiceContainer, get_services
from backend.contracts.models import (
    Job,
    JobDefinitionParam,
    ToolConfirmation,
    ToolConfirmationMode,
    ToolDefinition,
    ToolVisibility,
)
from backend.jobs.handlers import ToolImplementations


@dataclass(frozen=True)
class ToolImplementationSpec:
    implementation_name: str
    route_path: str
    func: Callable[..., Any]
    signature: inspect.Signature
    tool_definition: ToolDefinition


ROUTE_CACHE: dict[str, Callable[..., Any]] = {}
TOOL_IMPLEMENTATION_CACHE: dict[str, ToolImplementationSpec] = {}
INCLUDE_DRAFT_TOOL_IMPLEMENTATIONS_ENV = "LUNAR_ANALYST_INCLUDE_DRAFT_TOOL_IMPLEMENTATIONS"
INCLUDE_DRAFT_HANDLERS_ENV = INCLUDE_DRAFT_TOOL_IMPLEMENTATIONS_ENV


def _to_kebab_case(name: str) -> str:
    return name.replace("_", "-")


def _display_annotation(annotation: Any) -> str:
    if isinstance(annotation, type):
        return annotation.__name__
    rendered = str(annotation)
    if rendered.startswith("<class '") and rendered.endswith("'>"):
        return rendered[8:-2]
    return rendered


def _default_value_for_schema(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _build_param_list(signature: inspect.Signature) -> list[JobDefinitionParam]:
    params: list[JobDefinitionParam] = []
    for param in signature.parameters.values():
        has_default = param.default is not inspect._empty
        params.append(
            JobDefinitionParam(
                name=param.name,
                type=_display_annotation(param.annotation),
                required=not has_default,
                default=None if not has_default else _default_value_for_schema(param.default),
            )
        )
    return params


def _request_model_for_spec(spec: ToolImplementationSpec | None, *, signature: inspect.Signature, func: Callable[..., Any]) -> tuple[type[BaseModel], dict[str, Any]]:
    contract = getattr(func, "__contract__", None)
    request_type = getattr(contract, "request_type", None)
    if isinstance(request_type, type) and issubclass(request_type, BaseModel):
        return request_type, request_type.model_json_schema()

    resolved_hints = get_type_hints(func, include_extras=True)
    fields: dict[str, tuple[Any, Any]] = {}
    for param in signature.parameters.values():
        annotation = resolved_hints.get(param.name, param.annotation)
        default_value = ... if param.default is inspect._empty else _default_value_for_schema(param.default)
        fields[param.name] = (annotation, default_value)
    model_name = f"{func.__name__.title().replace('_', '')}Request"
    request_model = create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    return request_model, request_model.model_json_schema()


def _response_schema_for_func(func: Callable[..., Any]) -> tuple[str | None, dict[str, Any]]:
    contract = getattr(func, "__contract__", None)
    response_type = getattr(contract, "response_type", None)
    if response_type is None:
        return None, {"type": "object", "additionalProperties": True}
    if isinstance(response_type, type) and issubclass(response_type, BaseModel):
        return response_type.__name__, response_type.model_json_schema()
    return _display_annotation(response_type), TypeAdapter(response_type).json_schema()


def _build_tool_definition(*, implementation_name: str, route_path: str, func: Callable[..., Any], signature: inspect.Signature) -> ToolDefinition:
    contract = getattr(func, "__contract__")
    tool_meta = getattr(contract, "tool", None)
    visibility = ToolVisibility.ADVANCED if tool_meta is None else tool_meta.visibility
    confirmation = ToolConfirmation()
    if tool_meta is not None:
        confirmation = ToolConfirmation(
            mode=tool_meta.confirmation_mode,
            action_type=tool_meta.confirmation_action_type,
        )
    request_model, params_schema = _request_model_for_spec(None, signature=signature, func=func)
    response_model_name, outputs_schema = _response_schema_for_func(func)
    return ToolDefinition(
        tool_name=implementation_name if tool_meta is None else tool_meta.tool_name,
        title=implementation_name.replace("_", " ").strip() if tool_meta is None else tool_meta.title,
        description=getattr(contract, "description", ""),
        visibility=visibility,
        confirmation=confirmation,
        tags=["native", "implementation", *(list(tool_meta.tags) if tool_meta is not None else [])],
        handler_name=implementation_name,
        implementation_name=implementation_name,
        route_path=f"/api/v1{route_path}",
        params=_build_param_list(signature),
        params_schema=params_schema,
        outputs_schema=outputs_schema,
        request_model_name=request_model.__name__,
        response_model_name=response_model_name,
    )


def discover_tool_implementations(*, include_drafts: bool | None = None) -> dict[str, ToolImplementationSpec]:
    specs: dict[str, ToolImplementationSpec] = {}
    if include_drafts is None:
        include_drafts = os.getenv(INCLUDE_DRAFT_TOOL_IMPLEMENTATIONS_ENV, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    draft_names = set(getattr(ToolImplementations, "DRAFT_HANDLER_NAMES", ()))
    for name, func in inspect.getmembers(ToolImplementations, predicate=callable):
        if name.startswith("_"):
            continue
        if not hasattr(func, "__contract__"):
            continue
        contract = getattr(func, "__contract__", None)
        tool_meta = getattr(contract, "tool", None)
        is_draft = name in draft_names or (tool_meta is not None and tool_meta.visibility == ToolVisibility.DRAFT)
        if is_draft and not include_drafts:
            continue
        signature = inspect.signature(func)
        route_path = f"/jobs/{_to_kebab_case(name)}"
        specs[name] = ToolImplementationSpec(
            implementation_name=name,
            route_path=route_path,
            func=func,
            signature=signature,
            tool_definition=_build_tool_definition(
                implementation_name=name,
                route_path=route_path,
                func=func,
                signature=signature,
            ),
        )
    return specs


def _build_endpoint_signature(spec: ToolImplementationSpec) -> inspect.Signature:
    sig = spec.signature
    resolved_hints = get_type_hints(spec.func, include_extras=True)
    parameters: list[inspect.Parameter] = []
    for param in sig.parameters.values():
        annotation = resolved_hints.get(param.name, param.annotation)
        if param.default is inspect._empty:
            body_default = Body(...)
        else:
            body_default = Body(param.default)
        parameters.append(param.replace(annotation=annotation, default=body_default))

    parameters.append(
        inspect.Parameter(
            name="services",
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=ServiceContainer,
            default=Depends(get_services),
        )
    )
    return inspect.Signature(parameters=parameters, return_annotation=Job)


def _make_endpoint(spec: ToolImplementationSpec) -> Callable[..., Job]:
    async def endpoint(**kwargs: Any) -> Job:
        services: ServiceContainer = kwargs.pop("services")
        return services.job_service.run_typed_job(spec.implementation_name, kwargs)

    endpoint.__name__ = f"submit_{spec.implementation_name}_job"
    endpoint.__doc__ = f"Auto-generated job submission endpoint for `{spec.implementation_name}`."
    endpoint.__signature__ = _build_endpoint_signature(spec)  # type: ignore[attr-defined]
    return endpoint


def build_job_router() -> APIRouter:
    router = APIRouter(tags=["jobs"])
    discovered = discover_tool_implementations()
    TOOL_IMPLEMENTATION_CACHE.clear()
    TOOL_IMPLEMENTATION_CACHE.update(discovered)

    for implementation_name, spec in discovered.items():
        if implementation_name in ROUTE_CACHE:
            endpoint = ROUTE_CACHE[implementation_name]
        else:
            endpoint = _make_endpoint(spec)
            ROUTE_CACHE[implementation_name] = endpoint

        router.add_api_route(
            spec.route_path,
            endpoint,
            methods=["POST"],
            response_model=Job,
            name=f"submit_{implementation_name}",
        )

    return router


# Compatibility aliases retained for incremental migration.
HandlerSpec = ToolImplementationSpec
HANDLER_CACHE = TOOL_IMPLEMENTATION_CACHE


def discover_job_handlers(*, include_drafts: bool | None = None) -> dict[str, ToolImplementationSpec]:
    return discover_tool_implementations(include_drafts=include_drafts)
