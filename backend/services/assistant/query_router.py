from __future__ import annotations

import re
from dataclasses import dataclass

_PROCEDURAL_HINTS = {
    "how",
    "steps",
    "procedure",
    "workflow",
    "run",
    "generate",
    "create",
    "produce",
    "configure",
    "set",
    "api",
    "job",
    "tool",
    "script",
    "parameter",
    "arguments",
    "command",
}

_DOMAIN_HINTS = {
    "lunar",
    "moon",
    "surface",
    "temperature",
    "dataset",
    "science",
    "mission",
    "apollo",
    "regolith",
    "insolation",
    "illumination",
    "thermal",
    "orbit",
    "crater",
}


@dataclass(frozen=True)
class ChannelBudget:
    procedural: float
    domain: float

    def normalized(self) -> "ChannelBudget":
        total = max(0.0, float(self.procedural)) + max(0.0, float(self.domain))
        if total <= 0.0:
            return ChannelBudget(procedural=0.5, domain=0.5)
        return ChannelBudget(
            procedural=max(0.0, float(self.procedural)) / total,
            domain=max(0.0, float(self.domain)) / total,
        )


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    budget: ChannelBudget


def route_query(
    query: str,
    *,
    default_channel: str = "mixed",
    budget_procedural: ChannelBudget | None = None,
    budget_domain: ChannelBudget | None = None,
    budget_mixed: ChannelBudget | None = None,
) -> RouteDecision:
    text = str(query or "").strip().lower()
    if not text:
        return RouteDecision(intent="mixed", budget=(budget_mixed or ChannelBudget(0.5, 0.5)).normalized())
    tokens = set(re.findall(r"[a-z0-9_:/.-]+", text))
    procedural_hits = len(tokens.intersection(_PROCEDURAL_HINTS))
    domain_hits = len(tokens.intersection(_DOMAIN_HINTS))
    if procedural_hits > domain_hits:
        return RouteDecision(
            intent="procedural",
            budget=(budget_procedural or ChannelBudget(0.8, 0.2)).normalized(),
        )
    if domain_hits > procedural_hits:
        return RouteDecision(
            intent="domain",
            budget=(budget_domain or ChannelBudget(0.2, 0.8)).normalized(),
        )
    normalized_default = str(default_channel or "mixed").strip().lower()
    if normalized_default == "procedural":
        return RouteDecision(
            intent="procedural",
            budget=(budget_procedural or ChannelBudget(0.8, 0.2)).normalized(),
        )
    if normalized_default == "domain":
        return RouteDecision(
            intent="domain",
            budget=(budget_domain or ChannelBudget(0.2, 0.8)).normalized(),
        )
    return RouteDecision(intent="mixed", budget=(budget_mixed or ChannelBudget(0.5, 0.5)).normalized())
