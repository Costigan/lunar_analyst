# CUDA Viewshed Kernel Snapshot (2026-03-25)

This note preserves the original supercover ray-casting CUDA kernel variant used during ADR.0036 implementation work.

Purpose:
- Keep a restorable copy of the more complete but currently unstable kernel path.
- Allow iterative debugging without losing the original structure.

Location in code:
- A disabled preserved builder is now in [backend/jobs/handlers.py](D:\projects\lunar_analyst\backend\jobs\handlers.py) as `_build_raycast_kernel_snapshot_2026_03_25(...)`.
- It is intentionally not invoked by runtime code.

Preserved characteristics:
- Observer-centered ray casting.
- Configurable direction lattice.
- Sub-pixel stepping (`step_size_pixels`).
- Supercover-style in-segment traversal between successive step points.
- LOS slope horizon test with optional parabolic curvature drop.

The snapshot is kept in source (not only in git history) so it can be compared/restored directly while CUDA stability debugging continues.
