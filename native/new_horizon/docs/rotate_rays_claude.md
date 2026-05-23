Changes Made

   1. KernelParams - Extended with GammaCenter, DGammaDx, DGammaDy fields for Grid Convergence compensation
   2. CalculateRaySegments - Now computes Grid Convergence at tile center and its gradients (via finite differences at ±1 pixel offsets), returning this info alongside segments
   3. GPU Kernel (Compact Mode) - Each pixel now:
    - Computes δ = -(dGammaDx

    * dCol + dGammaDy * dRow)
    - Rotates all polynomial coefficients (A1-A4, B1-B4) by δ using a 2D rotation matrix
    - This aligns the ray to local True North instead of center's True North
   4. Test helper - Updated to handle the new 3-tuple return type

  All SyntheticDemTests and the emulator comparison test pass. The fix should eliminate the discontinuity artifact at patch boundaries by ensuring every pixel traces a ray in
  the correct True Azimuth direction.
