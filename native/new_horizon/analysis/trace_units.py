"""
Deep trace of the polynomial and distance calculation to understand the units mismatch.
"""

import numpy as np

# From the analysis, we know:
# - Polynomial samples s is in KILOMETERS (from BuildRaySamples: sample.s is km)
# - Polynomial samples pixel positions (x, y) are in PIXELS
# - mapRes is approximately 5 m/pixel

# Key values from the trace:
# First sample: s=0.001 km (1m), px=4106.2887, py=3165.8912
# Last sample:  s=1.124 km (1124m), px=4991.9991, py=2482.2602

# The pixel distance from first to last is:
dx_px = 4991.9991 - 4106.2887
dy_px = 2482.2602 - 3165.8912
pixel_dist = np.sqrt(dx_px**2 + dy_px**2)
print(f"Pixel distance first→last: {pixel_dist:.2f} pixels")

# The s distance from first to last is:
s_first = 0.001  # km
s_last = 1.124   # km
s_dist_km = s_last - s_first
s_dist_m = s_dist_km * 1000
print(f"s distance first→last: {s_dist_m:.2f} m")

# This gives us the effective resolution:
effective_res = s_dist_m / pixel_dist
print(f"Effective resolution (s_m / pixel): {effective_res:.4f} m/pixel")

# This is very close to 1.0 m/pixel, NOT 5.0 m/pixel!
# That means s is approximately equal to pixel distance, not map distance

print()
print("="*70)
print("UNDERSTANDING THE POLYNOMIAL FIT")
print("="*70)

# FitPlanarToChordCubicWithTerrain computes:
# planar[i] = (samples[i].x - x0) * mapRes     <- This is in METERS
# chordDist[i] = chord_i - chord_0             <- This is in METERS

# So for sample i:
# planar = pixel_dist * mapRes ≈ pixel_dist * 5.0 m/pixel
# chord = samples[i].s * 1000 m/km ≈ pixel_dist * 1.0 m/pixel

# The polynomial maps: planar (meters) → chord (meters)
# Since chord ≈ planar / 5, we'd expect c1 ≈ 0.2, c2 ≈ 0, c3 ≈ 0

print("Expected polynomial behavior:")
print("  planar = pixel_dist * 5.0")
print("  chord = pixel_dist * 1.0 (approximately)")
print("  So chord = planar * 0.2")
print("  Therefore c1 ≈ 0.2")

# At the 500m threshold in the emulator:
# s = 0.5 km → trueDist = s * 1000 = 500m (before threshold)
# pixel position: ~500 pixels from origin (since s ≈ pixel_dist)
# planarMeters = 500 pixels * 5 m/pixel = 2500m

# After threshold:
# trueDist = SStartChord*1000 + EvalPlanarChord(planarMeters)
# EvalPlanarChord(2500) ≈ 2500 * 0.2 = 500m (if c1=0.2)
# SStartChord = sStart = 0.001 km (from first sample)
# So trueDist ≈ 0.001*1000 + 500 = 501m

# This SHOULD be continuous! But the trace shows a jump from 499m to 186m.

print()
print("="*70)
print("CHECKING THE ACTUAL BEHAVIOR AT BOUNDARY")
print("="*70)

# At idx 1988 (just before threshold, s=0.4998 km):
# trueDist = s * 1000 = 499.8m (correct)

# At idx 1989 (just after threshold, s=0.5001 km):
# planarMeters = (px - X0) * mapRes
# px = 4499.9, X0 = 4106.3
# planarMeters = (4499.9 - 4106.3) * 5 = 393.6 * 5 = 1968m

# EvalPlanarChord(1968) should return approximately:
# 1968 * c1 + 1968^2 * c2 + 1968^3 * c3

# If c1 ≈ 0.2, then:
# EvalPlanarChord(1968) ≈ 394m

# trueDist = SStartChord*1000 + 394
# SStartChord = 0.001 (1m)
# trueDist ≈ 1 + 394 = 395m

# But the trace shows trueDist = 186m. This means either:
# 1. The polynomial coefficients are different than expected
# 2. SStartChord is negative or wrong
# 3. There's a bug in how EvalPlanarChord uses the coefficients

print("Analysis of boundary discontinuity:")
print()
print("Before threshold (s=0.4998 km):")
print("  trueDist = s * 1000 = 499.8m ✓")
print()
print("After threshold (s=0.5001 km):")
print("  planarMeters = (4499.9 - 4106.3) * 5 = 1968m")
print("  Expected EvalPlanarChord(1968) ≈ 1968 * 0.2 = 394m (if c1=0.2)")
print("  Expected trueDist = 0.001*1000 + 394 = 395m")
print("  Actual trueDist = 186m (from trace)")
print()
print("The discrepancy suggests the polynomial coefficients are NOT what we expect!")

print()
print("="*70)
print("HYPOTHESIS: The polynomial is fit wrong or evaluated wrong")
print("="*70)

# Let's compute what c1 would need to be to get 186m:
# 186 = SStartChord*1000 + EvalPlanarChord(1968)
# If SStartChord = 0.001:
# 186 = 1 + EvalPlanarChord(1968)
# EvalPlanarChord(1968) = 185m
# c1 * 1968 = 185
# c1 = 185 / 1968 = 0.094

print("For trueDist = 186m with planarMeters = 1968m:")
print("  EvalPlanarChord(1968) = 185m")
print("  If linear: c1 = 185 / 1968 = 0.094")
print()
print("But we expect c1 ≈ 0.2 (chord/planar ratio)")
print()
print("Wait... let me reconsider.")
print()

# Actually, the samples.s is in KILOMETERS and represents CHORD distance
# But the polynomial fit uses (samples[i].s * 1000 - chord0) as the target
# where chord0 is computed from samples[0].s

# At sample 0: s = 0.001 km = 1m
# At sample 10: s = 1.124 km = 1124m
# So chordDist range is ~0 to 1123m (after subtracting chord0)

# planar range is ~0 to 5600m (1120 pixels * 5 m/pixel)

# The polynomial maps planar (0-5600) → chordDist (0-1123)
# This is NOT a simple scaling! The polynomial is trying to capture a non-linear mapping.

# But wait - the chord distance should be LARGER than planar distance at long range
# due to spherical geometry. Something is fundamentally wrong here.

print("CRITICAL REALIZATION:")
print()
print("The polynomial maps planar(5600m) → chord(1123m)")
print("But physically, chord should be CLOSE TO planar at short ranges!")
print("The ratio chord/planar should be ~1.0, not ~0.2")
print()
print("This means the 's' in samples is NOT the true chord distance.")
print("It's something else - perhaps the tangent distance in the 3D tangent plane.")

# Looking back at BuildRaySamples, samples.s comes from:
# - TrySampleWithChordDistance which computes chord distance
# - But the samples are generated by walking along tangentDist

# Let me re-examine the ratio: pixel_dist / s_dist ≈ 1.0
# This means s ≈ pixel_dist in PIXELS
# But mapRes = 5 m/pixel
# So s(m) ≈ pixel_dist / 5

# AH HA! The issue is that s is in KILOMETERS but the ratio suggests s(km) ≈ pixel_dist/1000
# pixel_dist = 1120 pixels
# s = 1.12 km
# s/pixel_dist = 1.12/1120 = 0.001 km/pixel = 1 m/pixel

# But map resolution is 5 m/pixel, so there's a 5x discrepancy!
# This suggests the BuildRaySamples function is computing s incorrectly.

print()
print("="*70)
print("LIKELY ROOT CAUSE")
print("="*70)
print()
print("BuildRaySamples appears to be computing 's' (chord distance in km)")
print("with a 5x error. The computed chord distance is 5x smaller than expected.")
print()
print("Pixel-to-distance relationship:")
print("  Expected: 1120 pixels * 5 m/pixel = 5600m = 5.6 km")
print("  Actual s: 1.12 km")
print("  Ratio: 5.6 / 1.12 = 5.0")
print()
print("This 5x error in BuildRaySamples propagates through:")
print("1. The polynomial fit (maps 5600m planar → 1120m chord)")
print("2. The emulator's distance calculations at >500m")
print()
print("The emulator switches at s=0.5km, but s is computed 5x too small,")
print("so it switches when the true distance is only ~100m.")
