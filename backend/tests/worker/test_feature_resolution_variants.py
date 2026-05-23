from pathlib import Path
import pytest
from backend.services.assistant.entity_reference_resolver import EntityReferenceResolver
from backend.services.nomenclature_service import NomenclatureService, ensure_nomenclature_schema

@pytest.fixture
def nomenclature_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_nomenclature.db"
    ensure_nomenclature_schema(db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO lunar_features (name, clean_name, feature_type, center_x, center_y, importance_score) VALUES (?, ?, ?, ?, ?, ?)",
        ("Mons Malapert", "mons malapert", "Mons", 0.0, 0.0, 1.0)
    )
    conn.execute(
        "INSERT INTO lunar_features (name, clean_name, feature_type, center_x, center_y, importance_score) VALUES (?, ?, ?, ?, ?, ?)",
        ("Malapert", "malapert", "Crater", 10.0, 10.0, 0.8)
    )
    conn.commit()
    conn.close()
    return db_path

class MockServices:
    def __init__(self, db_path: Path):
        # EntityReferenceResolver constructs db_path = workspace_root / "scenario_catalog.db"
        self.stores = type('obj', (object,), {'workspace_root': str(db_path.parent)})

def test_resolve_feature_with_variants(nomenclature_db: Path):
    # Ensure the db file has the name the resolver expects
    target_db = nomenclature_db.parent / "scenario_catalog.db"
    if nomenclature_db != target_db:
        import shutil
        shutil.copy(nomenclature_db, target_db)
    
    resolver = EntityReferenceResolver(
        tool_services=MockServices(target_db),
        scenario_directory_resolver=lambda _: target_db.parent
    )
    
    # 1. Test "Mons Malapert" (Direct match)
    m1 = resolver._resolve_feature(mention="Mons Malapert", normalized="mons malapert", feature_type="Mons")
    assert m1.resolved_id == "feature:1"
    assert m1.strategy == "exact"
    assert m1.confidence == 1.0

    # 2. Test "Malapert" + "Mons" (Prefix variant match)
    # This simulates the LLM splitting "Mons Malapert" into mention="Malapert", type="Mons"
    m2 = resolver._resolve_feature(mention="Malapert", normalized="malapert", feature_type="Mons")
    assert m2.resolved_id == "feature:1"
    assert m2.strategy == "exact"
    assert m2.confidence == 1.0

    # 3. Test "Malapert" + "Mountain" (Alias + Prefix variant match)
    m3 = resolver._resolve_feature(mention="Malapert", normalized="malapert", feature_type="Mountain")
    assert m3.resolved_id == "feature:1"
    assert m3.strategy == "exact"
    assert m3.confidence == 1.0

    # 4. Test "Malapert" + "Crater" (Direct match for another feature)
    m4 = resolver._resolve_feature(mention="Malapert", normalized="malapert", feature_type="Crater")
    assert m4.resolved_id == "feature:2"
    assert m4.strategy == "exact"
    assert m4.confidence == 1.0

    # 5. Type prefix/suffix variants without explicit context type
    m5 = resolver._resolve_feature(mention="Crater Malapert", normalized="crater malapert", feature_type=None)
    assert m5.resolved_id == "feature:2"
    assert m5.strategy == "exact"
    assert m5.confidence == 1.0

    m6 = resolver._resolve_feature(mention="Malapert Crater", normalized="malapert crater", feature_type=None)
    assert m6.resolved_id == "feature:2"
    assert m6.strategy == "exact"
    assert m6.confidence == 1.0

    # 6. English type alias -> IAU type without explicit context type
    m7 = resolver._resolve_feature(mention="Malapert Mountain", normalized="malapert mountain", feature_type=None)
    assert m7.resolved_id == "feature:1"
    assert m7.strategy == "exact"
    assert m7.confidence == 1.0

from backend.services.assistant.intent_to_tool_planner import IntentToToolPlanner
from backend.services.assistant.prompt_classifier import SegmentClassification, SegmentOffsets


def test_personal_pronoun_not_treated_as_entity(nomenclature_db: Path):
    """Regression: 'Take me to Dawa Crater' was blocked by entity_ambiguity because
    'me' (dobj of 'take') was used as the preferred_target for feature resolution,
    fuzzy-matching feature names like 'Mee'/'Mendel'. Personal pronouns must be
    excluded from dep_meta, pos_mentions, and direct_object_candidate selection."""
    target_db = nomenclature_db.parent / "scenario_catalog.db"
    if nomenclature_db != target_db:
        import shutil
        shutil.copy(nomenclature_db, target_db)

    import sqlite3
    conn = sqlite3.connect(target_db)
    conn.execute(
        "INSERT OR IGNORE INTO lunar_features (name, clean_name, feature_type, center_x, center_y, importance_score) VALUES (?, ?, ?, ?, ?, ?)",
        ("Dawa", "dawa", "Crater", 100.0, 100.0, 0.65)
    )
    conn.commit()
    conn.close()

    resolver = EntityReferenceResolver(
        tool_services=MockServices(target_db),
        scenario_directory_resolver=lambda _: target_db.parent
    )

    classification = SegmentClassification(
        segment_id="s1",
        text="Take me to Dawa Crater.",
        offsets=SegmentOffsets(0, 23),
        segment_class="intent_family",
        confidence=1.0,
        classification_origin="test",
        intent_family="location_navigation",
        intent_properties={"operation": "goto", "feature_ref": "Dawa", "context_filter": "Crater"}
    )

    resolution = resolver.resolve_segment(
        classification=classification,
        scenario_id="scn_1",
        prior_mentions=[]
    )

    # "me" must not appear as a mention or ambiguity
    mention_texts = [m.mention_text.lower() for m in resolution.mentions]
    assert "me" not in mention_texts, f"'me' should not be an entity mention, got: {mention_texts}"
    ambiguity_texts = [a.get("mention_text", "").lower() for a in resolution.ambiguities]
    assert "me" not in ambiguity_texts, f"'me' should not cause an ambiguity, got: {ambiguity_texts}"

    # Dawa must resolve successfully and block must not fire
    assert any(m.resolved_id is not None and "dawa" in m.mention_text.lower() for m in resolution.mentions), \
        "Dawa should be resolved as a feature"
    assert resolution.ambiguities == [], f"No ambiguities expected, got: {resolution.ambiguities}"

    # End-to-end: planner should produce location.goto without clarification_required
    planner = IntentToToolPlanner()
    plan = planner.map(
        classification=classification,
        scenario_id="scn_1",
        entity_resolution=resolution
    )
    assert plan is not None
    assert not plan.requires_clarification, \
        f"Expected planned tool steps, got requires_clarification=True: {plan.clarification_message}"
    assert len(plan.tool_steps) == 1
    assert plan.tool_steps[0].tool_name == "location.goto"



    # Ensure the db file has the name the resolver expects
    target_db = nomenclature_db.parent / "scenario_catalog.db"
    if nomenclature_db != target_db:
        import shutil
        shutil.copy(nomenclature_db, target_db)

    resolver = EntityReferenceResolver(
        tool_services=MockServices(target_db),
        scenario_directory_resolver=lambda _: target_db.parent
    )
    
    # 1. Resolve the entity first
    classification = SegmentClassification(
        segment_id="s1",
        text="Zoom in to Mons Malapert.",
        offsets=SegmentOffsets(0, 25),
        segment_class="intent_family",
        confidence=1.0,
        classification_origin="test",
        intent_family="location_navigation",
        intent_properties={"operation": "goto", "feature_ref": "Malapert", "context_filter": "Mons"}
    )
    
    resolution = resolver.resolve_segment(
        classification=classification,
        scenario_id="scn_1",
        prior_mentions=[]
    )
    assert any(m.resolved_id == "feature:1" for m in resolution.mentions)

    # 2. Map it via planner
    planner = IntentToToolPlanner()
    plan = planner.map(
        classification=classification,
        scenario_id="scn_1",
        entity_resolution=resolution
    )
    
    assert plan is not None
    assert len(plan.tool_steps) == 1
    assert plan.tool_steps[0].tool_name == "location.goto"
    # CRITICAL: Verify feature_id is present in the arguments!
    assert plan.tool_steps[0].arguments["feature_id"] == "1"
    assert plan.tool_steps[0].arguments["name"] == "Malapert"
