from __future__ import annotations

from typing import Any, Callable


def execute_assistant_rag_ingest(
    *,
    scenario_id: str,
    relative_root: str,
    rebuild: bool,
    extensions: list[str] | None,
    respect_directives: bool,
    create_index: Callable[..., Any],
    emit_progress: Callable[[dict[str, Any]], None],
    is_cancel_requested: Callable[[], bool],
    cancellation_error_factory: Callable[[], Exception],
) -> dict[str, Any]:
    index = create_index(allowed_extensions=extensions)

    def _progress(payload: dict[str, Any]) -> None:
        if is_cancel_requested():
            raise cancellation_error_factory()
        emit_progress(
            {
                "stage": str(payload.get("stage", "index")),
                "message": str(payload.get("message", "")),
                "scanned": int(payload.get("scanned", 0) or 0),
                "added": int(payload.get("added", 0) or 0),
                "updated": int(payload.get("updated", 0) or 0),
                "skipped": int(payload.get("skipped", 0) or 0),
                "deleted": int(payload.get("deleted", 0) or 0),
            }
        )

    stats = index.refresh(
        relative_root=relative_root,
        rebuild=bool(rebuild),
        extensions=extensions,
        respect_directives=bool(respect_directives),
        progress=_progress,
    )

    return {
        "scenario_id": str(scenario_id or "global"),
        "relative_root": str(relative_root or ""),
        "db_path": str(index.db_path),
        "scanned": int(stats.get("scanned", 0)),
        "added": int(stats.get("added", 0)),
        "updated": int(stats.get("updated", 0)),
        "skipped": int(stats.get("skipped", 0)),
        "deleted": int(stats.get("deleted", 0)),
    }
