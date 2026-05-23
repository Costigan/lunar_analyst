from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.services.assistant.entity_reference_resolver import EntityReferenceResolver
from backend.services.assistant.prompt_classifier import SegmentClassification, SegmentOffsets
from backend.services.assistant.verb_normalizer import VerbNormalizer
from backend.services.nomenclature_service import ensure_nomenclature_schema


@dataclass(frozen=True)
class _Scenario:
    scenario_id: str
    scenario_root: str
    name: str
    directory: str


@dataclass(frozen=True)
class _Layer:
    layer_id: str
    title: str


class _ScenarioService:
    def __init__(self, scenario: _Scenario) -> None:
        self._scenario = scenario

    def list_scenarios(self) -> list[_Scenario]:
        return [self._scenario]

    def get_scenario(self, scenario_id: str) -> _Scenario:
        if scenario_id == self._scenario.scenario_id:
            return self._scenario
        raise KeyError(scenario_id)


class _LayerService:
    def list_layers(self, scenario_id: str) -> list[_Layer]:
        if scenario_id != "s1":
            return []
        return [_Layer(layer_id="layer_slope", title="Slope Layer")]


@dataclass
class _Stores:
    workspace_root: Path


@dataclass
class _ToolServices:
    scenario_service: _ScenarioService
    layer_service: _LayerService
    stores: _Stores


def _seed_feature(db_path: Path) -> None:
    ensure_nomenclature_schema(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO lunar_features(
                name, clean_name, feature_type, diameter_km, importance_score, description,
                center_x, center_y, min_x, min_y, max_x, max_y, origin_description
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Shackleton",
                "shackleton",
                "Crater",
                21.0,
                21.0,
                "South-pole crater",
                1.0,
                2.0,
                None,
                None,
                None,
                None,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO lunar_features(
                name, clean_name, feature_type, diameter_km, importance_score, description,
                center_x, center_y, min_x, min_y, max_x, max_y, origin_description
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Malapert",
                "malapert",
                "Mons",
                10.0,
                10.0,
                "Named mountain",
                3.0,
                4.0,
                None,
                None,
                None,
                None,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO lunar_features(
                name, clean_name, feature_type, diameter_km, importance_score, description,
                center_x, center_y, min_x, min_y, max_x, max_y, origin_description
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Mons Malapert",
                "mons malapert",
                "Mons",
                67.0,
                67.0,
                "Named mountain",
                3.5,
                4.5,
                None,
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_verb_normalizer_maps_synonym_to_canonical() -> None:
    normalizer = VerbNormalizer()
    result = normalizer.normalize(text="Zoom to Shackleton crater", input_operation=None)
    assert result.canonical_operation == "goto"
    assert result.ambiguous is False


def test_entity_resolver_resolves_feature_and_binds_pronoun(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    _seed_feature(workspace_root / "scenario_catalog.db")

    scenario_dir = workspace_root / "scenario_a"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "slope.tif").write_text("x", encoding="utf-8")

    services = _ToolServices(
        scenario_service=_ScenarioService(
            _Scenario(
                scenario_id="s1",
                scenario_root="scenario_a",
                name="Scenario A",
                directory=str(scenario_dir),
            )
        ),
        layer_service=_LayerService(),
        stores=_Stores(workspace_root=workspace_root),
    )
    resolver = EntityReferenceResolver(
        tool_services=services,
        scenario_directory_resolver=lambda sid: scenario_dir if sid == "s1" else None,
    )

    first = SegmentClassification(
        segment_id="s1",
        text="Find Shackleton crater",
        offsets=SegmentOffsets(start=0, stop=22),
        segment_class="intent_family",
        confidence=0.8,
        classification_origin="test",
        intent_family="location_navigation",
        intent_properties={"operation": "find", "feature_ref": "Shackleton", "context_filter": "Crater"},
    )
    second = SegmentClassification(
        segment_id="s2",
        text="Zoom to it",
        offsets=SegmentOffsets(start=23, stop=33),
        segment_class="intent_family",
        confidence=0.8,
        classification_origin="test",
        intent_family="location_navigation",
        intent_properties={"operation": "goto"},
    )

    resolved = resolver.resolve_segments(classifications=[first, second], scenario_id="s1")
    first_resolution = resolved["s1"]
    second_resolution = resolved["s2"]

    assert first_resolution.canonical_operation == "search"
    assert first_resolution.direct_object_candidate is not None
    assert first_resolution.target_kind == "feature"
    assert str(first_resolution.target_resolved_id or "").startswith("feature:")
    assert any(item.kind == "feature" and str(item.resolved_id or "").startswith("feature:") for item in first_resolution.mentions)
    assert second_resolution.canonical_operation == "goto"
    assert second_resolution.target_kind == "feature"
    assert any(
        item.kind == "feature"
        and item.strategy == "pronoun_from_turn_state"
        and str(item.resolved_id or "").startswith("feature:")
        for item in second_resolution.mentions
    )


def test_entity_resolver_resolves_scenario_layer_file_and_colormap(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    ensure_nomenclature_schema(workspace_root / "scenario_catalog.db")

    scenario_dir = workspace_root / "scenario_a"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "slope.tif").write_text("x", encoding="utf-8")

    services = _ToolServices(
        scenario_service=_ScenarioService(
            _Scenario(
                scenario_id="s1",
                scenario_root="scenario_a",
                name="Scenario A",
                directory=str(scenario_dir),
            )
        ),
        layer_service=_LayerService(),
        stores=_Stores(workspace_root=workspace_root),
    )
    resolver = EntityReferenceResolver(
        tool_services=services,
        scenario_directory_resolver=lambda sid: scenario_dir if sid == "s1" else None,
    )

    cls = SegmentClassification(
        segment_id="s1",
        text="Apply inferno to slope layer and describe slope.tif",
        offsets=SegmentOffsets(start=0, stop=50),
        segment_class="intent_family",
        confidence=0.8,
        classification_origin="test",
        intent_family="layer_style_update",
        intent_properties={
            "operation": "apply",
            "target": {"layer_ref": "slope"},
            "style": {"colormap_ref": "inferno"},
        },
    )

    resolved = resolver.resolve_segments(classifications=[cls], scenario_id="s1")["s1"]
    kinds = {item.kind for item in resolved.mentions if item.resolved_id}
    assert "layer" in kinds
    assert "colormap" in kinds


def test_entity_resolver_expansion_kinds(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    ensure_nomenclature_schema(workspace_root / "scenario_catalog.db")
    scenario_dir = workspace_root / "scenario_a"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "analysis.py").write_text("print('ok')", encoding="utf-8")
    services = _ToolServices(
        scenario_service=_ScenarioService(
            _Scenario(
                scenario_id="s1",
                scenario_root="scenario_a",
                name="Scenario A",
                directory=str(scenario_dir),
            )
        ),
        layer_service=_LayerService(),
        stores=_Stores(workspace_root=workspace_root),
    )
    resolver = EntityReferenceResolver(
        tool_services=services,
        scenario_directory_resolver=lambda sid: scenario_dir if sid == "s1" else None,
    )
    cls = SegmentClassification(
        segment_id="s1",
        text="Run tool location.search for prod_123 from source trek_catalog between 2026-01-01 and 2026-01-02 at x=1.0 y=2.0 and check job_42 with notebook analysis.py",
        offsets=SegmentOffsets(start=0, stop=120),
        segment_class="other",
        confidence=0.7,
        classification_origin="test",
    )
    resolved = resolver.resolve_segments(classifications=[cls], scenario_id="s1")["s1"]
    kinds = {item.kind for item in resolved.mentions if item.resolved_id}
    assert "product" in kinds
    assert "job" in kinds
    assert "tool" in kinds
    assert "dataset_or_source" in kinds
    assert "time_window" in kinds
    assert "coordinate" in kinds
    assert "notebook" in kinds


def test_entity_resolver_extracts_feature_from_raw_text_when_segment_is_other(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    _seed_feature(workspace_root / "scenario_catalog.db")
    scenario_dir = workspace_root / "scenario_a"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    services = _ToolServices(
        scenario_service=_ScenarioService(
            _Scenario(
                scenario_id="s1",
                scenario_root="scenario_a",
                name="Scenario A",
                directory=str(scenario_dir),
            )
        ),
        layer_service=_LayerService(),
        stores=_Stores(workspace_root=workspace_root),
    )
    resolver = EntityReferenceResolver(
        tool_services=services,
        scenario_directory_resolver=lambda sid: scenario_dir if sid == "s1" else None,
    )
    cls = SegmentClassification(
        segment_id="s1",
        text="Zoom in on the top of Mons Malapert and map slope to the same color map.",
        offsets=SegmentOffsets(start=0, stop=74),
        segment_class="other",
        confidence=0.6,
        classification_origin="test",
    )
    resolved = resolver.resolve_segments(classifications=[cls], scenario_id="s1")["s1"]
    assert resolved.canonical_operation == "goto"
    assert resolved.target_kind == "feature"
    assert str(resolved.target_resolved_id or "").startswith("feature:")
    assert any(
        item.kind == "feature" and str(item.resolved_id or "").startswith("feature:")
        for item in resolved.mentions
    )
    assert not any(
        item.kind == "untyped_noun" and item.normalized_ref in {"mons", "malapert", "mons malapert"}
        for item in resolved.mentions
    )


def test_entity_resolver_extracts_feature_from_name_type_order(tmp_path: Path) -> None:
    """Colloquial "NAME TYPE" phrases like "Shackleton Crater" must resolve like "Crater Shackleton"."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    _seed_feature(workspace_root / "scenario_catalog.db")
    scenario_dir = workspace_root / "scenario_a"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    services = _ToolServices(
        scenario_service=_ScenarioService(
            _Scenario(
                scenario_id="s1",
                scenario_root="scenario_a",
                name="Scenario A",
                directory=str(scenario_dir),
            )
        ),
        layer_service=_LayerService(),
        stores=_Stores(workspace_root=workspace_root),
    )
    resolver = EntityReferenceResolver(
        tool_services=services,
        scenario_directory_resolver=lambda sid: scenario_dir if sid == "s1" else None,
    )
    cls = SegmentClassification(
        segment_id="s1",
        text="Take me to Shackleton Crater.",
        offsets=SegmentOffsets(start=0, stop=28),
        segment_class="other",
        confidence=0.6,
        classification_origin="test",
    )
    resolved = resolver.resolve_segments(classifications=[cls], scenario_id="s1")["s1"]
    assert resolved.canonical_operation == "goto", f"expected goto, got {resolved.canonical_operation!r}"
    assert resolved.target_kind == "feature", f"expected feature, got {resolved.target_kind!r}"
    assert str(resolved.target_resolved_id or "").startswith("feature:"), (
        f"expected resolved feature id, got {resolved.target_resolved_id!r}"
    )
    feature_mentions = [item for item in resolved.mentions if item.kind == "feature" and item.resolved_id]
    assert feature_mentions, "no resolved feature mention found in entity resolution"
    assert any("shackleton" in str(item.normalized_ref or "").lower() for item in feature_mentions), (
        "Shackleton not resolved as feature"
    )
    assert not resolved.ambiguities, f"unexpected ambiguities: {resolved.ambiguities}"



    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    ensure_nomenclature_schema(workspace_root / "scenario_catalog.db")
    scenario_dir = workspace_root / "scenario_a"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    services = _ToolServices(
        scenario_service=_ScenarioService(
            _Scenario(
                scenario_id="s1",
                scenario_root="scenario_a",
                name="Scenario A",
                directory=str(scenario_dir),
            )
        ),
        layer_service=_LayerService(),
        stores=_Stores(workspace_root=workspace_root),
    )
    resolver = EntityReferenceResolver(
        tool_services=services,
        scenario_directory_resolver=lambda sid: scenario_dir if sid == "s1" else None,
    )
    cls = SegmentClassification(
        segment_id="s1",
        text="Compare illumination tradeoffs for landing safety.",
        offsets=SegmentOffsets(start=0, stop=47),
        segment_class="other",
        confidence=0.6,
        classification_origin="test",
    )
    resolved = resolver.resolve_segments(classifications=[cls], scenario_id="s1")["s1"]
    assert any(item.kind == "untyped_noun" and item.reason_code == "entity_pos_tagged_untyped" for item in resolved.mentions)


def test_entity_resolver_marks_show_slope_as_ambiguous_layer_or_file(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    ensure_nomenclature_schema(workspace_root / "scenario_catalog.db")
    scenario_dir = workspace_root / "scenario_a"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "slope.tif").write_text("x", encoding="utf-8")
    services = _ToolServices(
        scenario_service=_ScenarioService(
            _Scenario(
                scenario_id="s1",
                scenario_root="scenario_a",
                name="Scenario A",
                directory=str(scenario_dir),
            )
        ),
        layer_service=_LayerService(),
        stores=_Stores(workspace_root=workspace_root),
    )
    resolver = EntityReferenceResolver(
        tool_services=services,
        scenario_directory_resolver=lambda sid: scenario_dir if sid == "s1" else None,
    )
    cls = SegmentClassification(
        segment_id="s1",
        text="show the slope",
        offsets=SegmentOffsets(start=0, stop=14),
        segment_class="intent_family",
        confidence=0.8,
        classification_origin="test",
        intent_family="layer_visibility_update",
        intent_properties={"operation": "show"},
    )
    resolved = resolver.resolve_segments(classifications=[cls], scenario_id="s1")["s1"]
    assert resolved.canonical_operation == "show"
    assert resolved.target_kind == "ambiguous_layer_or_file"
    assert resolved.target_resolved_id is None
