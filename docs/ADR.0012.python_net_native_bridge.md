# ADR.0012: Python/.NET Native Bridge Strategy (pythonnet)

## Status
Accepted

## Context
Lunar Analyst performs high-fidelity lunar terrain and lighting analysis (e.g., horizon generation, hillshading, PSR mapping) that requires high-performance compute kernels. While the application control plane is written in Python (FastAPI), the heavy lifting is handled by a `.NET 9` compute library (`moonlib`) using `ILGPU` and `CUDA`.

A reliable, high-performance bridge was needed to:
1.  **Orchestrate Compute:** Launch C# compute kernels from Python job handlers.
2.  **Share Data:** Pass complex configuration objects and file paths between runtimes.
3.  **Ensure Stability:** Prevent native library conflicts (e.g., GDAL/PROJ DLL mismatches) and handle runtime crashes gracefully.

## Decision
We adopted **`pythonnet`** (Python for .NET) as the primary bridge technology, coupled with an **Isolated Subprocess Dispatcher** for long-running compute jobs.

### 1. Explicit Native Bootstrapping
To avoid common `DLLNotFoundException` and runtime initialization errors, we implemented a custom `native_bootstrap.py` layer.
-   **Runtime Selection:** Explicitly loads the `.NET 9` `coreclr` runtime using a resolved `moonlib.runtimeconfig.json`.
-   **DLL Resolver:** A strict custom resolver (`_configure_native_dll_search_paths`) preloads critical native dependencies (GDAL, PROJ, GEOS, CSPICE) in a deterministic order using `os.add_dll_directory` and `ctypes.WinDLL`.
-   **Dependency Preloading:** Managed assemblies are pre-registered using `clr.AddReference` to ensure all interop types are available before use.

### 2. Isolated Job Dispatcher
To maintain backend stability, compute jobs that use the native bridge are executed in an isolated process via `backend.worker.native_job_dispatcher`.
-   **Process Isolation:** Each native job runs in its own `sys.executable` subprocess. This prevents memory leaks, GDAL finalizer instabilities, or native crashes from taking down the main FastAPI server.
-   **Context-Based Execution:** Jobs are defined by a JSON context (handler name + arguments) and write their output to a result JSON file.
-   **Hard Exit:** The dispatcher uses `os._exit()` to ensure the process terminates immediately after completion, bypassing potentially unstable managed/native cleanup sequences.

### 3. Unified Job Contract
All native jobs are exposed through the standard `JobHandlers` class in `backend/jobs/handlers.py`.
-   **Typed Handlers:** Handlers use Pydantic models for both inputs and outputs, ensuring type safety across the bridge.
-   **Bridge Import:** Native code is accessed via `import_moonlib()`, which ensures the bootstrap has occurred before the `moonlib` namespace is imported.

### 4. GDAL Runtime Synchronization
Both Python and .NET runtimes share the same GDAL/PROJ data directories (`GDAL_DATA`, `PROJ_LIB`). The bootstrap layer synchronizes these environment variables to ensure both sides of the bridge see a consistent geospatial environment.

## Consequences
-   **Stability:** Crashes in high-performance C# kernels are contained within worker subprocesses.
-   **Performance:** `pythonnet` provides low-overhead interop for starting jobs, while the actual heavy lifting stays in the native runtime.
-   **Maintainability:** The `JobHandlers` contract provides a single point of truth for both the UI/API and the native worker.
-   **Complexity:** Managing the "Double Boot" (Python loading .NET, which then loads native C DLLs) requires careful path management and version pinning.
-   **Platform Dependency:** The current bootstrap and DLL resolver are heavily optimized for Windows (using `WinDLL` and `os.add_dll_directory`), which matches the primary target environment (Windows 11).
