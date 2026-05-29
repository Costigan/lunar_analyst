# Adding Test Coverage Reports

This note describes how to generate a coverage report for the default native C# regression tests.

The default native regression suite is `native/new_horizon/new_horizon.sln`. It includes `HorizonGen.Tests` and intentionally does not include `HorizonGen.Development.Tests`.

## One-Time Setup

Add the Coverlet collector to the regression test project:

```bash
dotnet add native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj package coverlet.collector
```

Install ReportGenerator if it is not already installed:

```bash
dotnet tool install --global dotnet-reportgenerator-globaltool
```

If the tool is already installed and needs an update:

```bash
dotnet tool update --global dotnet-reportgenerator-globaltool
```

## Generate Coverage

Run the default regression suite with coverage collection:

```bash
dotnet test native/new_horizon/new_horizon.sln \
  --collect:"XPlat Code Coverage" \
  --results-directory native/new_horizon/TestResults
```

Generate an HTML report:

```bash
reportgenerator \
  -reports:"native/new_horizon/TestResults/**/coverage.cobertura.xml" \
  -targetdir:"native/new_horizon/TestResults/coverage-report" \
  -reporttypes:Html
```

Open the report:

```text
native/new_horizon/TestResults/coverage-report/index.html
```

## What To Inspect

Start with the native horizon engine surfaces:

- `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`
- `native/new_horizon/moonlib/horizon/ElevationMap.cs`
- `native/new_horizon/moonlib/horizon/MoonSrsLambdaFactory.cs`
- `native/new_horizon/moonlib/horizon/HorizonCompressor.cs`
- `native/new_horizon/moonlib/horizon/HorizonTileStore.cs`
- `native/new_horizon/moonlib/pipeline/streaming/*`
- `native/new_horizon/moonlib/MoonlibBridge.cs`

Look at branch coverage as well as line coverage. For this project, important uncovered branches often matter more than an aggregate percentage.

High-value coverage gaps include:

- CRS parsing and unsupported projection paths.
- Pixel/CRS round-trip edge cases.
- DEM dimension validation.
- Patch boundary rounding and tile lookup conflicts.
- Horizon compression boundary values and invalid input paths.
- Streaming terminal states, chunk reassembly, and reducer errors.
- Progress and cancellation callback behavior.

## Interpreting Results

Coverage is a guide, not the definition of correctness. The native horizon code also needs scenario-driven regression coverage. See:

```text
docs/ADR.0060.native_horizon_regression_scenario_matrix.md
```

When a line or branch is uncovered, ask whether it belongs in one of the ADR.0060 regression scenarios. Prefer adding a small deterministic regression test over moving a broad development harness back into the default suite.

## Running Development Tests Separately

Algorithm-development tests are in:

```text
native/new_horizon/tests/HorizonGen.Development.Tests/
```

They are not part of the default solution because many are long-running, require private external DEMs, emit diagnostic artifacts, or are expected to fail while algorithm work is in progress.

Run them explicitly only when needed:

```bash
dotnet test native/new_horizon/tests/HorizonGen.Development.Tests/HorizonGen.Development.Tests.csproj
```

Do not use development-test coverage as the default regression coverage signal. If a development test protects stable behavior, reduce it to a small portable regression test and add that test to `HorizonGen.Tests`.

## Cleanup

Coverage output is written under:

```text
native/new_horizon/TestResults/
```

Remove that directory when the report is no longer needed:

```bash
rm -rf native/new_horizon/TestResults
```
