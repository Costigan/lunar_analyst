
* Subpatch Pipeline Migration Plan Created!

  * Executive Summary

  Goal: Convert current single-polynomial pipeline to use 16×16 subpatches (64 localized polynomial fits per patch) for significantly improved accuracy.

  * Key Changes Overview

  ┌────────────────┬───────────────────────┬───────────────────────────────┬────────────────────────────┐
  │ Aspect         │ Current               │ Target                        │ Impact                     │
  ├────────────────┼───────────────────────┼───────────────────────────────┼────────────────────────────┤
  │ Kernel         │ QuadTreeRayCastKernel │ QuadTreeSubpatchRayCastKernel │ Pre-compilation change     │
  ├────────────────┼───────────────────────┼───────────────────────────────┼────────────────────────────┤
  │ Segments/Patch │ 4,320                 │ 276,480                       │ 64× more (better accuracy) │
  ├────────────────┼───────────────────────┼───────────────────────────────┼────────────────────────────┤
  │ GPU Memory     │ 400KB total           │ 26MB total                    │ Acceptable for modern GPUs │
  ├────────────────┼───────────────────────┼───────────────────────────────┼────────────────────────────┤
  │ Polynomials    │ 1 per patch           │ 64 per patch                  │ Localized fitting          │
  ├────────────────┼───────────────────────┼───────────────────────────────┼────────────────────────────┤
  │ Configuration  │ Hardcoded             │ PIPELINE_SUBPATCH_SIZE = 16   │ User-adjustable            │
  └────────────────┴───────────────────────┴───────────────────────────────┴────────────────────────────┘

  * Implementation Phases

  Phase 1: Core Infrastructure

   - Add PIPELINE_SUBPATCH_SIZE = 16 constant
   - Switch kernel pre-compilation to subpatch version
   - Update buffer pool for 64× larger segment arrays
   - Change field names (_quadTreeKernel → _subpatchKernel)

  Phase 2: Pipeline Integration

   - Replace CalculateCompactRaySegments → CalculateSubpatchRaySegments
   - Update kernel launch parameters (isCompact → subpatchSize)
   - Maintain all existing stream pool and async optimizations

  Phase 3: Testing & Validation

   - Single patch correctness test
   - Full 1599-patch performance validation
   - GPU memory usage monitoring
   - Lightmap correctness verification

  * Performance Expectations

   - Accuracy: Significant improvement from 64 localized polynomial fits
   - Memory: ~26MB GPU RAM (acceptable for modern hardware)
   - Speed: Slower CPU segment calculation but GPU should remain efficient
   - Flexibility: Easy to experiment with 8×8, 32×32 subpatches later

  *️ Risk Mitigation

   - Monitor GPU memory usage (reduce workers from 8→4 if needed)
   - Fallback to 32×32 subpatches if 16×16 too memory-intensive
   - Profile CPU vs GPU bottlenecks and optimize accordingly

  The plan leverages existing subpatch code extensively while preserving all pipeline optimizations!
