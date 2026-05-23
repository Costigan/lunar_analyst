# How Segments Are Classified

This note describes how the assistant currently breaks a prompt into segments and classifies each segment before execution. It reflects the implementation in:

- `backend/services/assistant/prompt_segmenter.py`
- `backend/services/assistant/prompt_classifier.py`
- `backend/services/assistant/command_router.py`
- `backend/services/assistant/assistant_service.py`

If this document and the code disagree, the code is the source of truth and the document should be updated.

## Where This Happens In The Turn Pipeline

In `AssistantService._initialize_turn_hybrid_context`, the runtime does this in order:

1. Segment the prompt into ordered prompt segments.
2. Classify each segment.
3. Build a turn execution plan from those classifications.

The segmentation and classification stages are logged and emitted as stage events:

- `prompt_segmentation_completed`
- `prompt_classification_completed`

## Segmentation

Segmentation is handled by `PromptSegmenter.segment(prompt)`.

Each segment gets:

- `segment_id`
- `text`
- `start_char`
- `end_char`
- `is_imperative_candidate`
- `has_complexity_guard`
- `segmentation_confidence`

### Step 1: Sentence Boundaries

The segmenter uses the configured spaCy model for sentence splitting. spaCy and the configured model are required for this stage; there is no non-spaCy sentence-splitting fallback in the active implementation.

### Step 2: Clause Splitting Inside Sentences

After sentence splitting, the segmenter may split a sentence further on orchestration connectors:

- `and then`
- `then`
- `also`
- `next`
- `after that`

It only does this when the following text looks like a new clause beginning with an action-like word such as:

- `turn`
- `show`
- `hide`
- `set`
- `switch`
- `use`
- `list`
- `run`
- `launch`
- `import`
- `move`
- `describe`
- `write`
- `create`
- `explain`
- `suggest`
- `recommend`
- `compare`
- `compute`
- `calculate`

If no useful clause split is found, the sentence stays intact.

### Step 3: Special Handling For Coordinated Imperatives

There is a second splitter for commands like:

- `turn on slope and hillshade`
- `show illumination and hazard layers`

If a sentence starts with an imperative prefix such as `turn on`, `show`, `hide`, `set`, `switch`, or `create`, and contains `and`, the segmenter may expand that into multiple parallel imperative segments.

For example, a prompt like:

`turn on slope and hillshade`

can become:

- `turn on slope`
- `turn on hillshade`

This split is suppressed if later coordinated parts look clause-like or conditional, for example if they contain terms such as:

- `if`
- `when`
- `unless`
- `because`
- `while`
- `then`

### Step 4: Complexity Guard

The segmenter marks a segment as having a complexity guard if it contains any of these markers:

- `if`
- `when`
- `unless`
- `only if`
- `except`
- `while`
- `compare`
- `tradeoff`
- `best`
- `optimize` / `optimise`

This does two things:

1. It discourages further splitting.
2. It lowers segmentation confidence.

The practical effect is that analytical or conditional segments are more likely to stay intact and be routed to the model path later.

### Step 5: Imperative Candidate Detection

After splitting, each segment is marked as `is_imperative_candidate` if it starts with an imperative-looking verb such as:

- `set`
- `switch`
- `change`
- `use`
- `turn`
- `show`
- `hide`
- `list`
- `run`
- `launch`
- `cancel`
- `get`
- `import`
- `move`
- `describe`
- `write`
- `create`

This is just a signal. It does not execute anything by itself.

### Step 6: Small Fragment Merge

Very small fragments under 8 characters are merged back into the previous segment. This avoids pathological splits that create low-value pieces.

### Fallback Behavior

If segmentation throws an exception or produces no segments, `AssistantService` falls back to a single segment covering the whole prompt:

- `segment_id = "s1"`
- `text = full prompt`
- `start_char = 0`
- `end_char = len(prompt)`
- `segmentation_confidence = 0.5`

## Classification

Classification is handled by `PromptClassifier.classify(...)`.

It takes:

- the ordered segment list
- the current `scenario_id`
- the `HybridCommandRouter`

Each segment gets one primary label. The current implementation uses these labels:

- `router_candidate`
- `model_required`
- `clarification_or_policy_blocked`

It also records:

- `confidence`
- `matched_action_ids`
- `missing_required_slots`
- `blocking_reason_code`
- `requires_clarification`
- `classification_origin`

At the moment, `classification_origin` is always `rule_only`.

## How A Segment Is Classified

### Case 1: Complexity Guard Present

If `segment.has_complexity_guard` is true, the classifier does not try to route the segment deterministically.

It emits:

- `label = "model_required"`
- `matched_action_ids = []`
- `requires_clarification = false`
- `confidence = min(0.7, segmentation_confidence)`

This is the main path for conditionals, tradeoff questions, and more analytical phrasing.

### Case 2: Try Router Matching

If there is no complexity guard, the classifier asks the `HybridCommandRouter` whether the segment matches a known deterministic action.

It does that through `_plan_segment(...)`, which:

1. prefers the router's internal `_plan_segment(segment=..., scenario_id=...)` helper if available
2. otherwise falls back to `router.plan(prompt=...)` and uses the first matched action

If the router finds no action, the segment is classified as:

- `label = "model_required"`
- `matched_action_ids = []`
- `requires_clarification = false`
- `confidence = min(0.79, segmentation_confidence)`

### Case 3: Router Match Found

If the router finds a deterministic action, the classifier starts from:

`confidence = clamp(segmentation_confidence + 0.08, 0.5, 0.99)`

Then it applies two thresholds:

- `deterministic_min_confidence = 0.8`
- `clarification_min_confidence = 0.6`

#### 3A. High Confidence

If confidence is at least `0.8`, the segment becomes:

- `label = "router_candidate"`
- `blocking_reason_code = null`
- `requires_clarification = false`
- `matched_action_ids = [planned.action_id]`

This means the classifier believes the segment is a good deterministic-routing candidate.

#### 3B. Middle Band

If confidence is between `0.6` and `0.8`, the segment becomes:

- `label = "clarification_or_policy_blocked"`
- `blocking_reason_code = "policy_ambiguous_target"`
- `requires_clarification = true`
- `matched_action_ids = [planned.action_id]`

This is the current implementation's ambiguity band. It says: "this looks like a router-type request, but not confidently enough to run immediately."

#### 3C. Low Confidence

If confidence is below `0.6`, the segment falls back to:

- `label = "model_required"`
- `blocking_reason_code = null`
- `requires_clarification = false`

## What The Router Is Actually Doing

The classifier's deterministic signal comes from `HybridCommandRouter`.

The router:

1. loads action specs from the assistant action-router configuration
2. evaluates specs in priority order
3. rejects matches if:
   - the spec is marked `deny_if_complex` and the segment contains a complexity marker
   - a deny-pattern matches
   - the action would require agent substeps and those are disabled
4. tries regex patterns for each action
5. extracts slots from regex matches
6. normalizes slots for the specific action
7. returns a `PlannedAction` only if normalization succeeds

That means a segment is only classified as a `router_candidate` if it survives both:

- pattern matching
- slot normalization

For example, a router pattern may match text loosely, but classification will still fail if required pieces like a scenario reference, file path, or job id cannot be normalized safely.

## Current Relationship Between Classification And Execution

Classification is not execution.

The current flow is:

1. Segmenter creates segment objects.
2. Classifier labels them.
3. `TurnExecutionPlanBuilder` maps those labels to execution modes.
4. The deterministic executor still uses `HybridCommandRouter` action plans as the real execution authority.

In other words:

- segmentation decides how the prompt is chopped up
- classification decides which route each piece should take
- router execution still decides the actual deterministic tool steps

## Important Note About Terminology Drift

Older ADR text in this area may use earlier labels such as:

- `deterministic_candidate`
- `llm_required`
- `blocked`

That is not the current implementation.

The current code uses:

- `router_candidate`
- `model_required`
- `clarification_or_policy_blocked`

Any document or test using the older labels should be treated as stale unless it is explicitly describing historical behavior.

## ADR Terminology Drift

The following ADRs use terminology that is now partly out of date relative to the current implementation:

- `docs/ADR.0026.spacy_intent_unit_segmentation.md`
  - Still describes downstream routing in terms of older planner-oriented language.
  - The code now uses a turn execution-plan artifact and current classifier labels differ from some of the ADR-era wording around routing outcomes.

- `docs/ADR.0027.intent_classification_contract.md`
  - This ADR is the closest to current behavior, but parts of its rollout/integration language still describe classification as feeding a "planner" rather than the current execution-plan naming.
  - It should be reviewed to ensure every reference consistently matches `TurnExecutionPlanBuilder` and the updated contract surface.

- `docs/ADR.0028.turn_planner_json_contract.md`
  - The title still says "Turn Planner JSON Contract".
  - Internally, the code now uses execution-plan naming, and public contract strings were renamed to `turn_execution_plan_*` and `execution_plan_segments`.
  - The document also still preserves some planner-oriented framing for continuity, even though the implementation is explicitly not a search planner.

- `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`
  - Still refers to upstream "planner" contracts in places.
  - The surrounding implementation now uses execution-plan terminology, so the cross-ADR wording should be tightened.

- `docs/ADR.0031.assistant_performance_improvement_program.md`
  - Uses umbrella phrases like "planner contract" and "planner-driven execution".
  - Those should be read as historical program language; the current code uses execution-plan terminology and router-driven deterministic execution.

- `docs/ADR.0033.assistant_observability_and_failure_taxonomy.md`
  - This was partly updated, but it should be read carefully whenever event/code names are discussed because the public contract strings recently changed from `turn_planner_*` to `turn_execution_plan_*`.

- `docs/ADR.0042.non_deterministic_classification_and_product_graph.md`
  - Still uses some inherited "planner contract" language in extension points.
  - Conceptually it now extends the execution-plan contract, not a search-style planner.
