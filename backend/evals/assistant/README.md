# Assistant Eval Assets

## Files
- `run_benchmark.py`: CLI launcher for pytest-based assistant eval suites.
- `benchmark_core.py`: shared prediction/output helper functions used by the pytest harness.
- `score.py`: deterministic scorer for routing-contract prediction metrics.
- `../tests/evals/conftest.py`: pytest fixtures, setup/teardown, per-case execution, output writing.
- `../tests/evals/test_assistant_functional.py`: functional eval cases (one pytest function per case).
- `../tests/evals/test_assistant_domain.py`: domain eval cases (one pytest function per case).

## Case Source Of Truth
Assistant benchmark cases are defined directly as pytest functions (one function per case), not CSV/XLSX rows:
- `backend/tests/evals/test_assistant_functional.py`
- `backend/tests/evals/test_assistant_domain.py`

## Separate Benchmark Model Defaults
Benchmark runs can use separate defaults from app defaults via `config/lunar_analyst.toml`:
```toml
[backend.llm.evals]
default_provider = "ollama"
default_model = "gpt-oss:20b"
```
Priority order:
1. CLI `--provider` / `--model`
2. `[backend.llm.evals]` defaults
3. App defaults (`[backend.llm]`)

## Prediction Format
Provide one JSON object per line with fields:
- `id` (string)
- `mode` (`tool_call` | `clarify` | `respond`)
- `primary_tool` (string|null)
- `tool_calls` (array of `{ "name": str, "arguments": object }`)
- `repair_applied` (bool)
- `first_try_success` (bool)
- `unsafe_blocked` (bool)
- `quality_gate_applied` (bool; true when free-text response quality checks were evaluated)
- `quality_pass` (bool; false when response appears malformed/repetitive)
- `quality_flags` (array of quality issue codes)
- `quality_issue_count` (int)
- `source_references` (array of source reference objects from assistant metadata, when present)
- `source_reference_count` (int)
- `rag_context_text` (string; exact injected RAG context text captured during eval runs)
- `rag_context_chars` (int)
- `rag_context_capture_count` (int)
- `rag_context_captures` (array of per-iteration captures when multiple model iterations occur)
- `requested_provider_id` / `requested_model_id` (requested target)
- `final_provider_id` / `final_model_id` (model that produced the final response)
- `fallback_used` (bool)
- `attempted_models` (ordered provider/model attempts)
- `fallback_chain` (ordered fallback transitions and reasons)
- `turn_handling_mode` (assistant execution path, for example `ordered_segment_execution` or `model_tool_loop`)
- `intent_family_segments` (family + validation details extracted from execution plan metadata when available)
- `intent_families` (unique intent family names extracted for the turn)
- `prefilter_eligible` (bool|null; set when first tool call is `raster.transform`)
- `prefilter_failure_stage` (`parse_validate` | `binding_validate` | `build_plan` | `estimate_resources` | null)
- `prefilter_error_code` (string|null)

## Run
Generate predictions (live provider run):
```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite functional
```

Disable full RAG context capture (enabled by default in eval runs):
```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite functional --no-capture-rag-context
```

Generate predictions for domain suite:
```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite domain
```

Run planner-only smoke subset:
```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite functional --planner-only --max-cases 10 --output backend/evals/assistant/predictions_planner.jsonl
```

Override provider/model:
```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite functional --provider ollama --model gpt-oss:20b
```

Scenario selector can be either scenario id or root name:
```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite functional --scenario mons-mouton
```

Also write CSV/XLSX and human-readable outputs:
```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite functional --output backend/evals/assistant/predictions.jsonl --csv-out backend/evals/assistant/predictions.csv --xlsx-out backend/evals/assistant/predictions.xlsx --human-readable --human-readable-out backend/evals/assistant/predictions.txt
```

## Optional JSON Report
```bash
.venv/bin/python -m backend.evals.assistant.score --predictions <predictions.jsonl> --json-out backend/evals/assistant/score_report.json
```

Key prefilter metrics in `score_report.json`:
- `prefilter_eligibility_rate`
- `first_attempt_eligible_executed_success_rate`
- `one_repair_loop_recovery_rate`
- `prefilter_failure_taxonomy` (count by error code)

## Intent-Family Readiness Dashboard
Generate per-family readiness metrics (validation, mapping success, clarification, fallback rates):
```bash
.venv/bin/python -m backend.evals.assistant.intent_family_readiness --predictions backend/evals/assistant/predictions_functional.jsonl --json-out backend/evals/assistant/intent_family_readiness.json --md-out backend/evals/assistant/intent_family_readiness.md
```

Enforce readiness thresholds (for broad family enablement gates):
```bash
.venv/bin/python -m backend.evals.assistant.intent_family_thresholds --readiness-json backend/evals/assistant/intent_family_readiness.json --json-out backend/evals/assistant/intent_family_thresholds.json
```

Intent-family eval case catalog:
- `backend/evals/assistant/intent_family_benchmark_v1.jsonl`

## Model Leaderboard (Multi-Model Matrix)

Run a provider/model matrix and build ranked comparison outputs (JSON/CSV/Markdown):

```bash
.venv/bin/python -m backend.evals.assistant.leaderboard --suite functional --scenario mons-mouton
```

By default the script:
- discovers targets from `GET /api/v1/assistant/providers` and falls back to `config/lunar_analyst.toml`;
- excludes CLI providers (`codex_cli`, `gemini_cli`) unless you pass `--allow-cli-providers`;
- for `--suite functional`/`--suite all`, filters out models that advertise no tool capability (when metadata/probe is available);
- runs `run_benchmark` once per provider/model;
- scores each run against `functional_benchmark_v1.csv` for `--suite functional`;
- writes artifacts under `backend/evals/assistant/leaderboard_runs/<timestamp>/`.

Per model run directory now includes:
- `predictions.jsonl` (raw per-case records)
- `predictions.csv` (per-case tabular record with prompt, response, and tool_calls JSON)
- `predictions_human.txt` (one-line per-case summary)
- `case_details.md` (detailed human-readable case log)
- `run_benchmark.log.json` / `score.log.json` (full command output)

Useful options:
- `--target provider:model` (repeatable) to run explicit targets only.
- `--catalog-source config` to skip API catalog discovery.
- `--allow-cli-providers` to include `codex_cli` and `gemini_cli`.
- `--no-require-tool-capability` to disable tool-capability filtering.
- `--skip-score` for domain/all suites or smoke runs.
- `--max-models N` and `--max-cases N` to reduce runtime.

## Desktop UI Runner (Non-Web)

Launch a simple Tkinter desktop UI for interactive model/case selection and result review:

```bash
.venv/bin/python -m backend.evals.assistant.leaderboard_ui
```

UI capabilities:
- choose suite, scenario, model targets, and specific case ids;
- run leaderboard benchmarks for selected targets;
- browse timestamped past runs and load any prior run from the same output base;
- view per-model summary rows;
- inspect per-case result table (success, turn status, mode, duration, primary tool, error);
- inspect per-case quality status/flags to catch malformed free-text responses;
- inspect full per-case details (prompt, tool calls/args, response, sources, errors) directly in the UI.
