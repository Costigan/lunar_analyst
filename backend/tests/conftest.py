from __future__ import annotations

import os
import tempfile
from typing import Callable

import pytest

# Test collection imports backend.api.app in multiple modules; avoid import-time
# native preflight to prevent pythonnet/GDAL bootstrap collisions in pytest.
os.environ.setdefault("LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT", "1")
os.environ["LUNAR_ANALYST_WORKSPACE_ROOT"] = os.path.join(
    tempfile.gettempdir(),
    f"lunar_analyst_pytest_workspace_{os.getpid()}",
)
os.environ.setdefault("LUNAR_ANALYST_SHARED_HORIZON_NATIVE_TIMEOUT_SECONDS", "2")


@pytest.fixture
def prompt_segmenter_factory(monkeypatch) -> Callable[[], object]:  # noqa: ANN001
    def _factory():  # noqa: ANN202
        import spacy  # type: ignore
        from backend.services.assistant.prompt_segmenter import PromptSegmenter

        nlp = spacy.blank("en")
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer")
        monkeypatch.setattr(spacy, "load", lambda *args, **kwargs: nlp)
        return PromptSegmenter()

    return _factory
