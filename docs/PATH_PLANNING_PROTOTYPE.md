# Power-Aware Lunar Rover Path Planning Prototype

## Problem Statement

Build a route solver for a lunar rover moving over a gridded terrain model. The
solver must find feasible traverses from a start location to a goal location
while accounting for:

- Terrain constraints, especially slope.
- Time-varying sunlight and shadow.
- Energy consumed while driving.
- Energy consumed while stationary.
- Energy recovered by solar charging.
- Battery limits, fuel-cell or reserve limits, and operational safety margins.

This is not a standard shortest-path problem. A cell is not simply reachable or
unreachable. Reachability depends on the rover's time of arrival and power
state. The same map cell may be strategically useful in several different
states: early with low energy, late with high energy, high battery but lower
fuel-cell reserve, or lower total energy but better timing for an upcoming sun
window.

The solver should therefore be treated as a resource-constrained, time-dependent
path planning problem over a raster grid.

## Required Inputs

The prototype should support these inputs:

- A rectangular grid region from a lunar DEM or derived planning raster.
- Per-cell terrain attributes:
  - slope,
  - optional roughness or hazard flags,
  - optional permanent exclusion masks.
- A time-dependent sunlight model:
  - indexed by cell and time, or
  - generated from horizon data on demand.
- Rover configuration:
  - cell size,
  - driving speed,
  - driving power,
  - stationary power,
  - solar generation rate in full sun,
  - solar deployment delay,
  - battery capacity,
  - minimum allowed battery state,
  - fuel-cell or auxiliary energy capacity,
  - charge efficiency,
  - hard reserve constraints.
- A start location, start time, and initial power state.
- A goal location or goal region.
- An objective function, such as earliest arrival, maximum terminal energy, or
  maximum minimum reserve.

## Required Outputs

At minimum, the solver should produce:

- A feasible path as grid cells and/or georeferenced coordinates.
- A time and power profile along the path.
- Arrival time at the goal.
- Terminal battery and fuel-cell state.
- Minimum battery and fuel-cell reserve encountered.
- A reason if no route is found within configured limits.

For debugging and validation, it should also be able to emit:

- Reachability rasters by time step.
- Best-known arrival time rasters.
- Energy reserve rasters.
- Frontier size and active-tile statistics.
- Counts of generated, accepted, dominated, and pruned states.

## Movement Model

The grid should allow 8-connected movement unless mission rules require
otherwise.

For each move from cell `A` to neighbor cell `B`:

- Reject the move if `B` violates hard terrain constraints.
- Compute move distance:
  - cardinal move: `cell_size`,
  - diagonal move: `sqrt(2) * cell_size`.
- Compute duration from configured rover speed or a terrain-adjusted speed
  model.
- Compute drive energy from drive power and duration.
- Advance the rover clock by the move duration.
- Reduce battery first, or use the mission-approved discharge policy.
- Reject the move if the resulting power state violates hard constraints.

The first prototype can use constant speed and constant drive power. Later
versions should allow speed and power to depend on slope, terrain class, or
thermal constraints.

## Stationary and Charging Model

At any cell, the rover may wait. Waiting changes the state according to:

- stationary power consumption,
- solar deployment time,
- local sunlight intensity over the wait interval,
- battery capacity,
- fuel-cell charge efficiency,
- reserve policies.

The charging model should be explicit about whether the rover may:

- charge while partially shadowed,
- charge the fuel cell only after the battery is full,
- use fuel-cell energy to maintain survival loads,
- let any reserve go negative,
- deploy solar arrays automatically or only as a planned action.

The prototype may initially apply charging at fixed time boundaries, but the
architecture should allow event-based waiting over real sunlight intervals.

## State Representation

A state label should contain at least:

- grid position,
- arrival time,
- usable battery energy,
- fuel-cell or auxiliary energy,
- total energy,
- minimum reserve encountered,
- predecessor pointer,
- optional action that produced the state.

The important design choice is that each cell should not be limited to one
state. A single "best" state per cell is fast, but it is generally unsafe for
this problem. A later high-energy arrival may dominate in one future scenario,
while an earlier lower-energy arrival may dominate in another because it can
charge through a sunlight window.

The solver should store a bounded set of non-dominated labels per cell.

## Dominance and State Preference

A state `A` dominates state `B` at the same cell if `A` is no worse in every
future-relevant dimension and strictly better in at least one. A conservative
dominance rule might be:

- `A.arrival_time <= B.arrival_time`
- `A.battery_wh >= B.battery_wh`
- `A.fuel_cell_wh >= B.fuel_cell_wh`
- `A.min_reserve_wh >= B.min_reserve_wh`

If all are true and at least one is strict, `B` can be discarded.

This rule is safe but may keep many labels. To control memory, the prototype
should experiment with bounded label sets:

- exact Pareto labels with a hard cap per cell,
- time buckets with best energy per bucket,
- energy buckets with earliest arrival per bucket,
- epsilon dominance, where small differences are treated as equivalent,
- beam pruning, keeping only the best `N` labels by objective score.

Any approximate pruning must be treated as a planner heuristic, not an optimal
solver, unless there is a proof that the pruning preserves the chosen objective.

## Objective Functions

The solver should make the objective explicit. Plausible objectives include:

- find any feasible route,
- earliest arrival,
- maximum terminal total energy,
- maximum terminal battery energy,
- maximum minimum reserve over the route,
- lexicographic objective, for example:
  1. feasible without reserve violation,
  2. earliest arrival,
  3. maximum terminal energy,
  4. shortest distance.

The objective affects the queue ordering, the stopping condition, and the
dominance policy. The implementation should not stop merely because the goal is
first reached unless the search ordering proves that the first goal state is
optimal for the selected objective.

## Proposed Architecture

### 1. Problem Model

Create a small set of data types that are independent of GPU or file I/O:

- `GridGeometry`: width, height, cell size, coordinate transforms.
- `TerrainGrid`: slope and terrain masks.
- `SunlightProvider`: sunlight intensity for `(cell, time interval)`.
- `RoverModel`: movement, discharge, charging, and reserve policies.
- `PlanningRequest`: start, goal, time bounds, objective, and limits.
- `StateLabel`: compact power-aware state.
- `PlanResult`: path, timeline, metrics, and failure reason.

This layer should be deterministic and testable on small synthetic maps.

### 2. CPU Reference Solver

Build a correct CPU solver first. It does not need to be fast. It should:

- use the same state transition code as later accelerated implementations,
- implement exact Pareto dominance,
- support small maps and short time horizons,
- emit detailed traces for tests.

This reference solver is the oracle for GPU and approximate solvers.

### 3. Search Strategy

Use a label-setting or label-correcting search over `(cell, state label)`.

A practical first version:

- Maintain a priority queue of labels.
- Order by the selected objective lower bound, such as earliest arrival or
  estimated remaining travel time.
- Pop a label.
- Generate drive transitions to neighboring cells.
- Generate selected wait/charge transitions.
- Insert a generated label only if it is not dominated at its destination cell.
- Remove labels that the new label dominates.

For the wait action, avoid generating arbitrary waits. Generate waits to useful
events:

- next sunlight transition,
- battery full,
- enough energy for a configured move horizon,
- next planning time bucket,
- latest allowed departure.

### 4. Acceleration Strategy

After the CPU solver is validated, add acceleration in stages.

The current active-tile GPU approach is promising for reachability expansion.
It should be kept as an implementation candidate, but adapted to multi-label
state where possible.

Potential accelerated designs:

- Active-tile relaxation:
  - process only tiles touched by the current frontier,
  - keep fixed-size label slots per cell,
  - run local relaxation until tile convergence,
  - activate neighboring tiles when boundary cells change.
- Time-sliced dynamic programming:
  - process all labels or best bucketed labels at each time layer,
  - easier to parallelize,
  - potentially more memory intensive.
- Hybrid CPU/GPU:
  - CPU owns priority queues and label-set management,
  - GPU evaluates batches of movement and charging transitions.

The best architecture may be hybrid. GPU kernels are good at evaluating many
uniform transitions. CPU code is better at variable-sized priority queues,
Pareto sets, and debugging complex dominance logic.

### 5. Path Reconstruction

Each accepted label should carry a predecessor reference:

- previous cell,
- previous label slot or label id,
- action type,
- action duration,
- power deltas.

For GPU implementations, predecessor storage should be explicit and stable.
Avoid relying only on a direction byte if multiple labels may exist per cell.

### 6. Data and I/O Layer

Keep GDAL and map projection code outside the solver core.

Responsibilities:

- load slope and terrain rasters,
- load or generate sunlight rasters,
- map lat/lon to grid coordinates,
- write GeoJSON route output,
- write diagnostic GeoTIFF layers.

The solver should receive already-normalized arrays and providers. This keeps
the algorithm testable without large local datasets.

## Strengths of This Approach

- Models the real planning problem more faithfully than shortest path.
- Handles shadow driving and charging as first-class state transitions.
- Supports mission-specific objectives instead of hard-coding one preference.
- Pareto labels avoid discarding states that are temporarily worse but
  strategically better.
- CPU reference implementation gives a correctness baseline before optimizing.
- Active-tile or batched GPU execution can still be used after the state model
  is made explicit.
- Diagnostic rasters and traces make failures easier to understand.

## Weaknesses and Risks

- Multi-label search can grow quickly in memory and runtime.
- Exact Pareto dominance may retain too many labels in large, mixed
  sun-shadow regions.
- Approximate pruning can silently remove the only feasible route.
- Time-varying sunlight makes the problem sensitive to time discretization.
- GPU implementation is harder if every cell has a variable number of labels.
- A fixed-size label cap per cell may be necessary for GPU efficiency, but it
  introduces approximation.
- If the objective is not formalized early, the stopping condition and
  dominance rules can be wrong.
- The charging model can dominate results, so simplifying it too aggressively
  may produce routes that look feasible but are operationally invalid.

## Experiments Before Committing

### Correctness Experiments

- Build tiny synthetic maps with known optimal answers.
- Test all-shadow, all-sun, and alternating sun-shadow corridors.
- Test cases where an earlier lower-energy state beats a later higher-energy
  state.
- Test cases where a later higher-energy state beats an earlier lower-energy
  state.
- Verify that single-label pruning fails on at least one constructed case.
- Compare path reconstruction against the accepted label chain.

### Dominance and Pruning Experiments

- Measure labels retained per cell under exact Pareto dominance.
- Compare exact Pareto against:
  - time buckets,
  - energy buckets,
  - epsilon dominance,
  - fixed top-`N` labels per cell.
- Track whether approximate methods lose feasibility or change objective value.
- Identify a label cap that gives acceptable quality and predictable memory.

### Time Modeling Experiments

- Compare fixed time-step charging against event-based charging.
- Vary time bucket sizes and measure route quality.
- Stress cells near sunlight transition boundaries.
- Determine whether sunlight should be sampled at arrival time, averaged over
  wait intervals, or integrated from a time series.

### Performance Experiments

- Establish CPU reference performance on small, medium, and large regions.
- Profile transition generation, dominance checks, and queue operations.
- Test active-tile propagation with one label per cell as a baseline.
- Test fixed-label-slot GPU kernels, such as 2, 4, 8, or 16 labels per cell.
- Measure frontier sparsity over realistic route requests.
- Measure memory bandwidth and occupancy for the GPU state layout.

### Operational Experiments

- Run routes through real slope and sunlight data.
- Check whether generated paths exploit unrealistic wait/charge behavior.
- Evaluate sensitivity to rover power constants.
- Evaluate sensitivity to reserve requirements.
- Compare candidate routes with domain-expert expectations.

## Recommended Prototype Sequence

1. Define the rover model and state transition functions in plain C#.
2. Implement a CPU reference solver with exact Pareto dominance.
3. Create a suite of synthetic correctness tests.
4. Add real raster loading behind simple provider interfaces.
5. Run small real-map experiments and inspect diagnostic outputs.
6. Experiment with bounded label approximations.
7. Choose the smallest approximation that preserves route quality on test cases.
8. Implement acceleration only after the state representation and pruning policy
   are stable.

## Initial Recommendation

Do not commit immediately to a fully GPU-resident solver. First prove the state
model, dominance policy, charging model, and objective on a CPU reference
implementation. Then use the current active-tile GPU design as a performance
candidate, likely with a bounded number of labels per cell.

The core architectural bet should be multi-label, power-aware search with clear
dominance semantics. The optimization bet should remain flexible until
experiments show whether active-tile GPU relaxation, time-sliced dynamic
programming, or a CPU/GPU hybrid gives the best tradeoff for realistic lunar
planning cases.
