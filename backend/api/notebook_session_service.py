from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from backend.api.dependencies_constants import REQUIRE_NOTEBOOK_SESSION_ENV
from backend.api.dependency_helpers import load_app_config as _load_app_config
from backend.api.dependency_helpers import utc_now as _utc_now
from backend.api.store_models import NotebookSessionRecord
from backend.contracts.models import NotebookSession


class NotebookSessionService:
    def __init__(self, stores: Any) -> None:
        self._stores = stores

    def create_session(self, client_name: str) -> NotebookSession:
        now = _utc_now()
        session = NotebookSessionRecord(
            session_id=f"nbs_{uuid4().hex[:12]}",
            api_token=f"nbt_{uuid4().hex}",
            client_name=client_name,
            created_at_utc=now,
            last_seen_at_utc=now,
        )
        self._stores.notebook_sessions[session.session_id] = session
        self._stores.notebook_sessions_by_token[session.api_token] = session.session_id
        return self._to_model(session)

    def get_session(self, session_id: str) -> NotebookSession:
        session = self._stores.notebook_sessions.get(session_id)
        if session is None:
            raise KeyError(f"Notebook session not found: {session_id}")
        return self._to_model(session)

    def validate_token(self, token: str | None) -> NotebookSession | None:
        if token is None or not token.strip():
            return None
        session_id = self._stores.notebook_sessions_by_token.get(token)
        if session_id is None:
            return None
        session = self._stores.notebook_sessions.get(session_id)
        if session is None:
            return None
        session.last_seen_at_utc = _utc_now()
        return self._to_model(session)

    def is_auth_required(self) -> bool:
        env = os.getenv(REQUIRE_NOTEBOOK_SESSION_ENV)
        if env is not None and env.strip():
            return env.strip().lower() in {"1", "true", "yes", "on"}
        config = _load_app_config()
        backend_cfg = config.get("backend", {})
        notebook_cfg = backend_cfg.get("notebook", {}) if isinstance(backend_cfg, dict) else {}
        required = notebook_cfg.get("require_session_token", False) if isinstance(notebook_cfg, dict) else False
        return bool(required)

    def _to_model(self, record: NotebookSessionRecord) -> NotebookSession:
        return NotebookSession(
            session_id=record.session_id,
            api_token=record.api_token,
            client_name=record.client_name,
            created_at_utc=record.created_at_utc,
            last_seen_at_utc=record.last_seen_at_utc,
        )
