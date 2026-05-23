from __future__ import annotations

from backend.contracts.assistant_models import AssistantMessage


def compact_messages(
    messages: list[AssistantMessage],
    *,
    max_messages_to_compact: int,
) -> tuple[str, int]:
    if not messages:
        return "", 0
    target = messages[:max_messages_to_compact]
    if not target:
        return "", 0
    lines: list[str] = []
    for msg in target:
        role = msg.role.value.upper()
        text = msg.content.strip().replace("\n", " ")
        if len(text) > 240:
            text = f"{text[:240]}..."
        lines.append(f"[{role}] {text}")
    summary = "Compacted session context:\n" + "\n".join(lines)
    return summary, len(target)
