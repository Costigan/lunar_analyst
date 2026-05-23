from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import ToolConfirmationMode, ToolVisibility


@dataclass(frozen=True)
class ToolContractMetadata:
    tool_name: str
    title: str
    visibility: ToolVisibility
    confirmation_mode: ToolConfirmationMode
    confirmation_action_type: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MethodContract:
    """Metadata for a typed service method contract."""

    name: str
    request_type: Any | None
    response_type: Any | None
    description: str = ""
    tool: ToolContractMetadata | None = None


CONTRACT_REGISTRY: dict[str, MethodContract] = {}


def contract(
    *,
    name: str | None = None,
    request_type: Any | None = None,
    response_type: Any | None = None,
    description: str = "",
    tool_name: str | None = None,
    tool_title: str | None = None,
    tool_visibility: ToolVisibility | None = None,
    confirmation_mode: ToolConfirmationMode = ToolConfirmationMode.NEVER,
    confirmation_action_type: str | None = None,
    tool_tags: tuple[str, ...] = (),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach contract metadata to a method and register it."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        contract_name = name or func.__qualname__
        tool_meta: ToolContractMetadata | None = None
        if tool_visibility is not None:
            resolved_tool_name = tool_name or func.__name__
            tool_meta = ToolContractMetadata(
                tool_name=resolved_tool_name,
                title=tool_title or resolved_tool_name.replace("_", " ").strip(),
                visibility=tool_visibility,
                confirmation_mode=confirmation_mode,
                confirmation_action_type=confirmation_action_type,
                tags=tuple(tool_tags),
            )
        CONTRACT_REGISTRY[contract_name] = MethodContract(
            name=contract_name,
            request_type=request_type,
            response_type=response_type,
            description=description,
            tool=tool_meta,
        )
        setattr(func, "__contract__", CONTRACT_REGISTRY[contract_name])
        return func

    return decorator
