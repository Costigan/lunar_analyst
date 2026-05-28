# ADR.0058: Partitioned Horizon Tile Store

- Status: Proposed
- Date: 2026-05-28
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `native/new_horizon/horizon/Program.cs`, `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`, `native/new_horizon/moonlib/horizon/HorizonFile.cs`, `native/new_horizon/moonlib/MoonlibBridge.cs`, `native/new_horizon/moonlib/pipeline/streaming/LightmapArrayStreamingBridge.cs`, `native/new_horizon/moonlib/pipeline/streaming/LightmapArrayStreamingBridge.V2.cs`, `native/new_horizon/moonlib/pipeline/LightmapPipeline.cs`, `native/new_horizon/moonlib/mapops/MapOperations.cs`, `native/new_horizon/moonlib/util/HorizonFileIndex.cs`

## Context

Native horizon generation writes one file per 128x128 DEM patch. Horizon filenames currently follow:

```text
horizon_<Y>_<X>_<elevation>.[bin|cbin]
```

where:

- `Y` is the tile row / DEM pixel Y coordinate, formatted with 5 digits,
- `X` is the tile column / DEM pixel X coordinate, formatted with 5 digits,
- `elevation` is observer height in decimeters, formatted with 3 digits.

Example:

```text
horizon_21504_20480_000.cbin
```

Current generation commonly writes all horizon files directly under one directory such as `horizons/`. Large production runs on the National Research Platform Kubernetes cluster have produced approximately 47,000 horizon files in a single CephFS-backed PersistentVolumeClaim directory. This causes poor metadata performance for simple directory operations such as `ls`, file globbing, and top-level `Directory.GetFiles` / `Directory.EnumerateFiles` scans.

## Problem

The flat horizon directory layout makes routine filesystem operations scale with the full horizon tile count in one CephFS directory. This is especially visible on shared RWMany volumes where metadata operations are relatively expensive.

We need a layout and access layer that:

1. avoids very large single horizon directories,
2. preserves the existing filename identity and coordinate convention,
3. supports existing flat directories for reads during migration,
4. provides a migration operation from flat to partitioned layout,
5. centralizes horizon path construction, existence checks, reading, writing, and enumeration,
6. keeps compressed `.cbin` horizons as the preferred default output.

## Decision

Introduce a `HorizonTileStore` service in the C# `moonlib.horizon` namespace to own horizon tile naming, layout, path resolution, reads, writes, existence checks, and enumeration.

New horizon writes will use a partitioned-by-Y layout:

```text
horizons/
  21504/
    horizon_21504_20480_000.cbin
```

The subdirectory name is the zero-padded 5-digit `Y` value. The filename remains unchanged.

Reads and enumeration must support both:

- new partitioned layout files, and
- legacy flat files directly under the horizon root.

When both compressed and uncompressed files exist for the same tile, the store must prefer `.cbin` over `.bin`.

## Scope

In scope:

- C# `HorizonTileStore` service for path and file operations.
- Partitioned-by-Y default write layout.
- Backward-compatible reads from flat and partitioned layouts.
- A new `horizon` CLI command to convert an existing flat horizon directory into the partitioned layout.
- Integration into horizon generation skip/write logic and downstream horizon consumers.
- Tests for path construction, parsing, read fallback, compressed preference, enumeration, migration, and generator skip behavior.

Out of scope:

- Changing the horizon binary or compressed file format.
- Changing tile size or horizon sample count.
- Reprojecting or changing DEM coordinate semantics.
- Introducing a database-backed horizon index.
- Kubernetes manifest or PVC changes.
- Removing legacy flat read support in this ADR.

## Normative Design

### 1. Coordinate and Filename Contract

The canonical horizon filename format is:

```text
horizon_{tileY:D5}_{tileX:D5}_{observerElevationDecimeters:D3}.{extension}
```

The filename coordinate order is `Y`, then `X`.

The service API should use explicit parameter names such as `tileY` and `tileX`. Call sites that currently use `tileRow`, `tileCol`, `TileY`, or `TileX` must map those values explicitly to avoid positional ambiguity.

Observer elevation conversion should preserve current behavior unless intentionally changed in a separate decision:

```csharp
var observerElevationDecimeters = (int)(observerElevationMeters * 10);
```

### 2. Store API

Add a service similar to:

```csharp
public enum HorizonTileLayout
{
    Flat,
    PartitionedByY
}

public readonly record struct HorizonTileKey(
    int TileY,
    int TileX,
    int ObserverElevationDecimeters);

public sealed class HorizonTileStore
{
    public HorizonTileStore(
        string rootDirectory,
        HorizonTileLayout writeLayout = HorizonTileLayout.PartitionedByY,
        bool readLegacyFlatFiles = true);

    public string BuildFileName(int tileY, int tileX, float observerElevationMeters, bool compress = true);
    public string BuildRelativePath(int tileY, int tileX, float observerElevationMeters, bool compress = true);
    public string BuildPath(int tileY, int tileX, float observerElevationMeters, bool compress = true);

    public string? FindExistingPath(int tileY, int tileX, float observerElevationMeters);
    public bool Exists(int tileY, int tileX, float observerElevationMeters);

    public void Write(int tileY, int tileX, float observerElevationMeters, ReadOnlySpan<float> data, bool compress = true);
    public float[] Read(int tileY, int tileX, float observerElevationMeters);

    public IEnumerable<string> EnumerateFiles(float? observerElevationMeters = null);
    public IEnumerable<(HorizonTileKey Key, string Path)> EnumerateTiles(float? observerElevationMeters = null);

    public static bool TryParseFileName(string path, out HorizonTileKey key);
}
```

The exact API may vary, but these responsibilities must remain centralized in one service rather than duplicated across generator, bridge, lightmap, PSR, and utility classes.

### 3. Write Behavior

Writes must default to compressed output:

```csharp
Write(tileY, tileX, observerElevationMeters, data, compress: true)
```

For partitioned writes, the service must create the Y subdirectory before writing.

Writes should use a temporary file in the same target directory and then atomically move it into the final path after the full write succeeds. This prevents interrupted jobs from leaving corrupt or zero-byte final horizon files that later resume logic could mistake for completed tiles.

Example:

```text
horizons/21504/horizon_21504_20480_000.tmp.cbin
horizons/21504/horizon_21504_20480_000.cbin
```

The final rename/move must stay within the same directory so POSIX filesystems can provide atomic replacement semantics.

### 4. Existing File Resolution

`FindExistingPath()` must check all supported read locations and formats for a tile:

1. partitioned `.cbin`,
2. partitioned `.bin`,
3. legacy flat `.cbin`,
4. legacy flat `.bin`.

Compressed files are preferred because generation normally writes `.cbin` and compressed horizons are the desired steady-state format. There should normally never be both `.bin` and `.cbin` for the same tile, but the resolution order must be deterministic.

### 5. Enumeration Contract

Enumeration must return valid horizon files from both layouts. Call sites must not use ad hoc top-level-only globs such as:

```csharp
Directory.EnumerateFiles(horizonDir, "horizon_*_000.*bin", SearchOption.TopDirectoryOnly)
```

Instead, downstream consumers must use the store enumeration API and then filter by elevation through parsed keys.

Enumeration should avoid scanning unrelated subdirectories. For the partitioned layout, only directories whose names are valid 5-digit Y values should be scanned.

### 6. Flat-to-Partitioned Conversion Command

Add a new command to `native/new_horizon/horizon/Program.cs` that converts a flat horizon directory to the partitioned layout.

Suggested command:

```bash
dotnet run --project native/new_horizon/horizon -- partition-horizons <horizons_directory>
```

The command must:

1. scan only top-level files matching valid horizon filenames,
2. create the corresponding Y subdirectory,
3. move each file into that subdirectory,
4. leave files already in partitioned subdirectories unchanged,
5. be idempotent,
6. report counts for moved, skipped, invalid, and conflicted files.

Conflict behavior:

- If the destination file already exists and has the same size as the source, skip or remove the duplicate only if explicitly implemented and tested.
- If the destination file already exists and differs, do not overwrite by default. Report a conflict and leave the source file in place.

### 7. Compatibility

Existing flat horizon directories remain readable. This is required for already-generated scenario outputs and for incremental rollout on NRP PVCs.

New writes should use the partitioned layout by default. A flat write layout may remain available as a test or transition option, but production generation should not continue creating large flat horizon directories.

### 8. Observability

Horizon generation progress events may continue reporting the base filename for UI compactness. Logs should include the full resolved output path when useful for diagnostics.

The conversion command must log:

- source root,
- layout target,
- number of files moved,
- number skipped,
- number invalid,
- number conflicted,
- elapsed time.

## Consequences

Positive consequences:

- Reduces CephFS metadata pressure in large horizon sets.
- Keeps file identity human-readable and coordinate-derived.
- Makes read/write behavior testable through one service.
- Enables safe migration of existing flat horizon directories.
- Preserves backward compatibility for existing outputs.

Negative consequences:

- Consumers that directly enumerate horizon files must be updated.
- Some tests and docs currently assume flat output paths and will need updates.
- Mixed flat and partitioned directories can exist during transition, so resolution order must remain deterministic.

## Implementation Plan

- [ ] Add `HorizonTileStore` and related value types under `native/new_horizon/moonlib/horizon/`.
- [ ] Move or wrap filename construction/parsing currently in `QuadTreeHorizonGenerator` so the store is the canonical implementation.
- [ ] Update `HorizonFile` only as needed to remain the low-level `.bin` / `.cbin` codec, not the layout owner.
- [ ] Update `QuadTreeHorizonGenerator.GenerateHorizons` and `GenerateHorizonsForPatches` to write through `HorizonTileStore` with `compress: true` default behavior where applicable.
- [ ] Update `QuadTreeHorizonGenerator.RemoveCompletedPatches` to use `HorizonTileStore.FindExistingPath()`.
- [ ] Update `MoonlibBridge` overwrite/skip logic to use `HorizonTileStore.FindExistingPath()` instead of top-level filename sets.
- [ ] Update `LightmapArrayStreamingBridge` and `LightmapArrayStreamingBridge.V2` to enumerate through `HorizonTileStore`.
- [ ] Update `LightmapPipeline` to enumerate through `HorizonTileStore`.
- [ ] Update PSR/lightmap map operations in `MapOperations` to enumerate through `HorizonTileStore`.
- [ ] Update `HorizonFileIndex` to scan both `.bin` and `.cbin` through `HorizonTileStore` and support partitioned directories.
- [ ] Add `partition-horizons` command to `native/new_horizon/horizon/Program.cs`.
- [ ] Implement idempotent flat-to-partitioned conversion with conflict reporting and no default overwrites.
- [ ] Add unit tests for filename parsing, path construction, compressed preference, flat fallback, partitioned enumeration, and migration conflicts.
- [ ] Update existing tests that assert flat output paths to use the store or assert partitioned paths.
- [ ] Run `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`.
- [ ] Manually verify a small generated horizon set can be consumed by lightmap/PSR code from the partitioned layout.

## Rollback

Rollback is straightforward because the horizon file payload format does not change.

If the new layout causes issues:

1. keep the read-compatible `HorizonTileStore` in place,
2. temporarily configure writes to flat layout if that option remains exposed,
3. move files from Y subdirectories back to the root with a one-off script if necessary.

No database migration or scenario schema migration is required.
