"""
Deep dive into the polynomial fitting issue.
The QT emulator is producing pixel coordinates that are WAY off.
"""

import pandas as pd
import numpy as np
import os

output_dir = r"/d/projects/new_horizon/output_debug"

# Load the QT trace for DEM0
qt0 = pd.read_csv(os.path.join(output_dir, "diag_qt_dem0.csv"))

print("="*70)
print("INVESTIGATING PIXEL COORDINATE ANOMALY")
print("="*70)

# Observer position
obs_x, obs_y = 4105.5, 3166.5
azimuth_deg = 51.75
azimuth_rad = np.radians(azimuth_deg)

# Expected direction in pixel space (approximate, ignoring projection effects for now)
# Azimuth 51.75° is NE direction
# dx = sin(az), dy = -cos(az) for North-up, East-right, Y increasing downward
dx_per_m = np.sin(azimuth_rad) / 5.0  # ~5m per pixel
dy_per_m = -np.cos(azimuth_rad) / 5.0

print(f"Observer: ({obs_x}, {obs_y})")
print(f"Azimuth: {azimuth_deg}°")
print(f"Expected direction: dx={dx_per_m:.4f}, dy={dy_per_m:.4f} per meter")
print()

# Look at the extreme slope samples
print("EXTREME SLOPE SAMPLES (where slope > 1000):")
print("dist_m     pixel_x    pixel_y    elevation    slope         expected_px  expected_py")
extreme = qt0[qt0['slope'] > 1000].copy()
for _, row in extreme.head(20).iterrows():
    dist = row['dist_m']
    px, py = row['pixel_x'], row['pixel_y']
    elev = row['elevation_m']
    slope = row['slope']
    
    # Expected pixel position
    exp_px = obs_x + dist * dx_per_m * 5  # Undo the /5.0
    exp_py = obs_y + dist * dy_per_m * 5
    
    px_err = px - exp_px
    py_err = py - exp_py
    
    print(f"{dist:8.2f}  {px:10.4f}  {py:10.4f}  {elev:10.2f}  {slope:12.0f}  {exp_px:10.2f}  {exp_py:10.2f}  err=({px_err:.0f}, {py_err:.0f})")

print()
print("="*70)
print("CHECKING DISTANCE VS PIXEL POSITION CONSISTENCY")
print("="*70)

# Compute actual distance from observer based on pixel coordinates
# (This ignores projection distortion but gives a sanity check)
qt0['pixel_dist_from_obs'] = np.sqrt((qt0['pixel_x'] - obs_x)**2 + (qt0['pixel_y'] - obs_y)**2)

# Map resolution is ~5m per pixel
map_res = 5.0  # approximate
qt0['approx_dist_m'] = qt0['pixel_dist_from_obs'] * map_res

print("Sample of distances - reported vs computed from pixels:")
print("idx   dist_m   pixel_dist(px)  approx_dist(m)  ratio")
for i in range(0, min(len(qt0), 2500), 100):
    row = qt0.iloc[i]
    ratio = row['approx_dist_m'] / row['dist_m'] if row['dist_m'] > 0 else 0
    flag = " ***" if abs(ratio - 1.0) > 0.5 else ""
    print(f"{i:4}  {row['dist_m']:8.1f}  {row['pixel_dist_from_obs']:14.1f}  {row['approx_dist_m']:14.1f}  {ratio:6.2f}{flag}")

# Find where the ratio becomes very wrong
print()
print("="*70)
print("FINDING WHERE POLYNOMIAL GOES WRONG")
print("="*70)

# Look at transitions
prev_ratio = 1.0
for i in range(len(qt0)):
    row = qt0.iloc[i]
    if row['dist_m'] < 0.1:
        continue
    ratio = row['approx_dist_m'] / row['dist_m']
    if abs(ratio - prev_ratio) > 1.0:  # Big jump
        print(f"Jump at index {i}:")
        print(f"  dist={row['dist_m']:.1f}m, px=({row['pixel_x']:.1f}, {row['pixel_y']:.1f})")
        print(f"  approx_dist={row['approx_dist_m']:.1f}m, ratio={ratio:.2f}")
        print(f"  prev_ratio={prev_ratio:.2f}")
        break
    prev_ratio = ratio

# Check the polynomial samples
print()
print("="*70)
print("POLYNOMIAL SAMPLE ANALYSIS")
print("="*70)

samples_path = os.path.join(output_dir, "diag_qt_dem0.samples.txt")
with open(samples_path) as f:
    lines = f.readlines()

print(f"Number of polynomial fit samples: {len(lines)}")
print()
print("Polynomial samples with distance from observer:")
print("s(m)        px          py          pixel_dist    approx_dist  s/approx")
for line in lines:
    parts = line.strip().split(':')
    if len(parts) == 3:
        s_km, px, py = float(parts[0]), float(parts[1]), float(parts[2])
        s_m = s_km * 1000
        pixel_dist = np.sqrt((px - obs_x)**2 + (py - obs_y)**2)
        approx_dist = pixel_dist * map_res
        ratio = s_m / approx_dist if approx_dist > 0 else 0
        print(f"{s_m:8.1f}  {px:10.4f}  {py:10.4f}  {pixel_dist:12.1f}  {approx_dist:12.1f}  {ratio:6.2f}")
