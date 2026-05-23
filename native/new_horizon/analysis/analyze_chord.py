"""
Analyze what's happening with the planar-to-chord polynomial.
"""

import pandas as pd
import numpy as np
import os

output_dir = r"/d/projects/new_horizon/output_debug"
qt0 = pd.read_csv(os.path.join(output_dir, "diag_qt_dem0.csv"))

# The trace includes: dist_m, pixel_x, pixel_y, x_local_km, z_local_m, x_local_m
# We can compute planarMeters from pixel positions

obs_x, obs_y = 4105.5, 3166.5  # observer pixel position
map_res = 4.99  # meters per pixel (from earlier analysis)

# Segment starting position from samples
seg_X0, seg_Y0 = 4106.2887, 3165.8912  # from first polynomial sample

# Compute planarMeters for each sample
qt0['dx_px'] = qt0['pixel_x'] - seg_X0
qt0['dy_px'] = qt0['pixel_y'] - seg_Y0
qt0['planar_px'] = np.sqrt(qt0['dx_px']**2 + qt0['dy_px']**2)
qt0['planar_m'] = qt0['planar_px'] * map_res

print("="*70)
print("PLANAR DISTANCE VS REPORTED DISTANCE")
print("="*70)

print("\nSamples around the 500m boundary (where formula switches):")
print("idx    s(km)     planar_m    dist_m    s*1000   planar/dist")

boundary_samples = qt0[(qt0['dist_m'] > 450) & (qt0['dist_m'] < 550)]
for i, (_, row) in enumerate(boundary_samples.iterrows()):
    s_km = row['dist_m'] / 1000.0  # This is wrong - dist_m is already in meters
    # Actually, let me trace through the code logic:
    # s is the parameter (in km)
    # trueDist is what gets written to dist_m
    # When s < 0.5 km: trueDist = s * 1000 (so dist_m = s in meters)
    # When s >= 0.5 km: trueDist = SStartChord*1000 + EvalPlanarChord(planarMeters)
    ratio = row['planar_m'] / row['dist_m'] if row['dist_m'] > 0 else 0
    print(f"{boundary_samples.index[i]:4}  {row['dist_m']/1000:.4f}  {row['planar_m']:10.1f}  {row['dist_m']:10.1f}  ratio={ratio:.4f}")

print("\n" + "="*70)
print("ANALYZING THE DISTANCE JUMP AT idx 1989")
print("="*70)

# Before jump: idx 1988 has dist=499.81
# After jump: idx 1989 has dist=186.55

idx_before = 1988
idx_after = 1989

row_before = qt0.iloc[idx_before]
row_after = qt0.iloc[idx_after]

print(f"\nBefore jump (idx {idx_before}):")
print(f"  dist_m = {row_before['dist_m']:.2f}")
print(f"  pixel = ({row_before['pixel_x']:.4f}, {row_before['pixel_y']:.4f})")
print(f"  planar_m = {row_before['planar_m']:.2f}")

print(f"\nAfter jump (idx {idx_after}):")
print(f"  dist_m = {row_after['dist_m']:.2f}")
print(f"  pixel = ({row_after['pixel_x']:.4f}, {row_after['pixel_y']:.4f})")
print(f"  planar_m = {row_after['planar_m']:.2f}")

# The issue: s is the PARAMETER to the polynomial (in km)
# Before jump: s was presumably ~0.5 km (500m), so trueDist = s * 1000 = 500m
# After jump: s crosses 0.5 km threshold, so formula changes to:
#   trueDist = SStartChord*1000 + EvalPlanarChord(planarMeters)
# 
# If EvalPlanarChord returns a SMALLER value than expected, trueDist would drop

print("\n" + "="*70)
print("HYPOTHESIS: PlanarToChord polynomial has wrong coefficients")
print("="*70)

# The polynomial EvalPlanarChord(p) = C1*p + C2*p^2 + C3*p^3
# For planarMeters around 500-600m, this should return approximately the same as planarMeters
# (since at short distances, chord ≈ planar)

# At idx 1989:
# planarMeters = row_after['planar_m'] ≈ 2016m (from pixel distance)
# But the reported trueDist = 186.55m
# So EvalPlanarChord(2016) ≈ 186.55 - SStartChord*1000

# If SStartChord is around 0.001 (1m in km), then:
# EvalPlanarChord(2016) ≈ 185.55

# This is WAY too small! For p=2016m, we'd expect chord ≈ 2016m
# The polynomial is returning 185m instead of ~2016m

# Possible issues:
# 1. Coefficients are wrong scale (fit to different units?)
# 2. The polynomial is not monotonic - it might wrap around
# 3. SStartChord is not what we think

print("\nComputing expected EvalPlanarChord behavior:")
print("If C1≈1, C2≈0, C3≈0, then EvalPlanarChord(p) ≈ p")
print(f"For planar_m = {row_after['planar_m']:.1f}m, expected chord ≈ {row_after['planar_m']:.1f}m")
print(f"But actual dist_m = {row_after['dist_m']:.1f}m")
print(f"Difference = {row_after['planar_m'] - row_after['dist_m']:.1f}m")

# This suggests the polynomial coefficients are incorrect
# OR the SStartChord is being subtracted/added wrongly

print("\n" + "="*70)
print("CHECKING: What if planarMeters calculation is wrong?")
print("="*70)

# The code computes:
# float planarDx = (px - seg.X0) * mapResFloat;
# float planarDy = (py - seg.Y0) * mapResFloat;
# float planarMeters = sqrt(planarDx*planarDx + planarDy*planarDy);

# At idx 1989:
px = row_after['pixel_x']
py = row_after['pixel_y']
# seg.X0, seg.Y0 should be the STARTING PIXEL position of the segment

# From samples file, first sample is at s=1m, px=4106.2887, py=3165.8912
# So seg.X0 = 4106.2887, seg.Y0 = 3165.8912

dx_px = px - seg_X0
dy_px = py - seg_Y0
print(f"At idx {idx_after}:")
print(f"  px = {px:.4f}, py = {py:.4f}")
print(f"  seg.X0 = {seg_X0:.4f}, seg.Y0 = {seg_Y0:.4f}")
print(f"  dx_px = {dx_px:.4f}, dy_px = {dy_px:.4f}")
print(f"  planar_px = {np.sqrt(dx_px**2 + dy_px**2):.4f}")
print(f"  planar_m (if mapRes=5) = {np.sqrt(dx_px**2 + dy_px**2) * 5:.1f}m")

# At 500m distance with 5m/pixel, we'd expect ~100 pixels from origin
# But the pixel is at 4499.7 - 4106.3 = 393 pixels from seg.X0
# That's 393 * 5 = 1965m of planar distance

# So when s = 0.5km (500m), the PLANAR distance is already ~2000m
# That means the polynomial mapping is highly non-linear
# Or s is in different units than expected
