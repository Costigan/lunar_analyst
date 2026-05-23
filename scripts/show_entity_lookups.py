#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.dependencies import build_service_container
from backend.services.assistant.prompt_classifier import SegmentClassification, SegmentOffsets

def _parse_args() -> argparse.Namespace:
    default_input = REPO_ROOT / "scripts" / "sample_entity_lookups.txt"
    parser = argparse.ArgumentParser(
        description="Read entities from sample_entity_lookups and show resolution results as the app would."
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        default=str(default_input),
        help="Path to the file containing entity names (one per line).",
    )
    parser.add_argument(
        "--scenario-id",
        default="s1",
        help="Scenario ID to use for context (default: s1).",
    )
    return parser.parse_args()

def main() -> int:
    args = _parse_args()
    input_path = Path(args.input_file).resolve()
    if not input_path.exists():
        print(f"Error: Input file {input_path} not found.", file=sys.stderr)
        return 1

    entities = [line.strip() for line in input_path.read_text().splitlines() if line.strip()]

    services = build_service_container()
    assistant = services.assistant_service

    # Entity lookup only: use the runtime entity resolver directly and avoid
    # prompt classification/model calls.
    resolver = assistant._entity_resolver  # noqa: SLF001

    header = f"{'PROMPT ENTITY':<25} | {'MENTIONS & RESOLUTION'}"
    print(header)
    print("-" * len(header) + "-" * 40)

    for index, entity in enumerate(entities, start=1):
        classification = SegmentClassification(
            segment_id=f"s{index}",
            text=entity,
            offsets=SegmentOffsets(start=0, stop=len(entity)),
            segment_class="intent_family",
            confidence=1.0,
            classification_origin="entity_lookup_script",
            intent_family="location_navigation",
            intent_properties={"feature_ref": entity},
        )
        classifications = [classification]

        resolved = resolver.resolve_segments(
            classifications=classifications,
            scenario_id=args.scenario_id,
        )

        res_list = []
        for res in resolved.values():
            for mention in res.mentions:
                m_str = f"[{mention.kind}: {mention.mention_text}"
                if mention.resolved_id:
                    m_str += f" -> {mention.resolved_id}"
                else:
                    m_str += " (unresolved)"
                m_str += "]"
                res_list.append(m_str)

        mentions_str = ", ".join(res_list) if res_list else "None"
        print(f"{entity[:25]:<25} | {mentions_str}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
