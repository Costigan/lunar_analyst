 🔍 Key Changes Made Since Pipelining Implementation

  Here's a high-level summary of the major architectural changes:

  1. API Change: Synchronous → Async

   - Changed GenerateHorizonsForPatches from void to async Task
   - All dependent code now uses await

  2. Processing Granularity: Patch-Level → DEM-Level

   - Before: Process entire patch (3 DEMs together) as one unit
   - After: Break each patch into 3 separate DEM work items, process individually, then merge results
   - Added DemWorkItem and DemResult classes for individual DEM tracking

  3. Pipeline Architecture: Sequential → 3-Stage Producer-Consumer

   - Stage 1: Producer creates individual DEM work items (CPU ray segment calculation)
   - Stage 2: 8 GPU workers process individual DEM passes concurrently
   - Stage 3: Merger collects DEM results, merges per patch, writes files

  4. GPU Execution: Single Default Stream → 8 Concurrent Streams

   - Added ConcurrentStack<AcceleratorStream> stream pool (8 streams)
   - Each GPU worker gets its own dedicated stream for true parallelism
   - Replaced _accelerator.Synchronize() with stream.Synchronize()

  5. Kernel Management: Per-Call Loading → Pre-Compiled

   - Moved LoadAutoGroupedKernel from LaunchSingleDemAsync to constructor
   - Kernel loaded once and reused for all 4,797 calls (saves ~24 seconds)

  6. Memory Management: Per-Call Allocation → Buffer Pool

   - Added BufferPool class for reusable GPU memory buffers
   - Buffers allocated once and reused across all operations

  7. Data Flow: Direct Processing → FIFO Channels

   - Added bounded channels (size 32) between pipeline stages
   - Channel<DemWorkItem> and Channel<DemResult> for coordination

  8. DEM Data Handling: Interleaved → Extracted

   - Added ExtractDemSegments() to separate individual DEM data from interleaved arrays
   - Added MergeDemResults() to recombine individual DEM results into patch results

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

  🤔 Potential Root Cause Areas:

  The "mostly black lightmaps" suggests incorrect horizon data, which could stem from:

   1. DEM Segment Extraction Bug: Incorrectly separating interleaved DEM data
   2. Result Merging Bug: Incorrectly combining individual DEM results back to patches
   3. Stream Synchronization: Race conditions with concurrent streams
   4. Buffer Reuse: Incorrect buffer sharing between concurrent operations
   5. Work Item Mapping: Wrong DEM index or patch coordinates in pipeline

  The fundamental change from "process 3 DEMs together" to "process each DEM separately then merge" is the most likely culprit.
