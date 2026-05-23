# TO FIX

Current priority list based on the latest `pytest -q` run in the work tree.

## 1. Fix assistant turn execution regression: `compacted_summary` is undefined

Symptoms:
- Many assistant worker tests now fail with `response.turn.status == "failed"` where they previously completed or requested confirmation.
- The clearest direct failure is `test_ordered_other_segment_provider_failure_marks_turn_failed`, which reports:
  - `NameError: name 'compacted_summary' is not defined`
- Affected tests include:
  - hybrid metadata tests
  - tool-loop completion tests
  - confirmation-gate tests
  - provider fallback and retry tests
  - domain-handoff tests

Likely root cause:
- A refactor in `backend/services/assistant/assistant_service.py` introduced a new handoff/context path and left a variable reference (`compacted_summary`) outside its scope.
- That exception aborts the assistant turn before normal completion, confirmation handling, or fallback logic can run.

Why this is first:
- It is a broad, blocking regression that causes many downstream failures independently of other behavior changes.

## 2. Restore prompt-segmentation compatibility with existing routing expectations

Symptoms:
- `test_segmenter_does_not_propagate_create_to_save_clause` now returns one unsplit segment instead of splitting `create ...` and `save ...`.
- `test_parser_fast_path_synthesizes_write_run_script_for_slope_mask_prompt` now splits a command prompt into too many fragments (`Write`, `run a script ...`, `generate ...`, `Output ...`) instead of preserving the expected command-shaped chunking.
- Several assistant tool-loop tests are likely affected indirectly because the router sees different segment boundaries and therefore different classifications / fast-path decisions.

Likely root cause:
- The `backend/services/assistant/prompt_segmenter.py` refactor moved from the prior imperative/coordinating-conjunction logic to a dependency-based splitter that does not preserve the old contract.
- The new splitter is not equivalent to the previous heuristics, especially for command turns and mixed imperative clauses.

Why this is second:
- This is a routing regression, not just a cosmetic one, and it can change which execution path the assistant takes.
- It is likely contributing to some of the failures that are not explained by the `compacted_summary` crash.

## 3. Re-run the assistant worker suite after the above fixes

Symptoms:
- There are many failures in `backend/tests/worker/test_assistant_tool_loop.py` and `backend/tests/worker/test_assistant_hybrid_metadata.py`.
- These are likely a mix of:
  - the `NameError` cascade from item 1
  - the segmentation contract drift from item 2

Likely root cause:
- These are downstream effects rather than separate primary bugs.

Suggested verification sequence:
1. Fix the `compacted_summary` crash.
2. Restore the segmenter contract for known prompt patterns.
3. Run the assistant worker tests that failed here, then widen to the full suite.

## Notes

- The bug-report capture / offline analysis ADR implementation does not appear to be the source of the failures in this test run.
- The failure set is concentrated in assistant routing and tool-loop execution.
