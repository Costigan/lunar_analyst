"""
Investigate the distance jump around index 2000 where things go wrong.
"""

import pandas as pd
import numpy as np
import os

output_dir = r"/d/projects/new_horizon/output_debug"
qt0 = pd.read_csv(os.path.join(output_dir, "diag_qt_dem0.csv"))

print("="*70)
print("SAMPLES AROUND INDEX 2000 (where distance jumps)")
print("="*70)

# Look at samples 1990-2020
print("idx    dist_m    pixel_x    pixel_y    elevation    slope        s_change")
prev_dist = 0
for i in range(1990, min(2030, len(qt0))):
    row = qt0.iloc[i]
    s_change = row['dist_m'] - prev_dist
    flag = " ***" if s_change < 0 else ""
    print(f"{i:4}  {row['dist_m']:8.2f}  {row['pixel_x']:10.4f}  {row['pixel_y']:10.4f}  {row['elevation_m']:10.2f}  {row['slope']:12.6f}  {s_change:+10.2f}{flag}")
    prev_dist = row['dist_m']

# The polynomial samples span from 1m to 1123.9m
# So at around 1123m, we might hit the end of the polynomial's valid range

print()
print("="*70)
print("CHECKING POLYNOMIAL EVALUATION BEYOND ITS RANGE")
print("="*70)

# Load polynomial samples
samples_path = os.path.join(output_dir, "diag_qt_dem0.samples.txt")
with open(samples_path) as f:
    lines = f.readlines()

samples = []
for line in lines:
    parts = line.strip().split(':')
    if len(parts) == 3:
        s_km, px, py = float(parts[0]), float(parts[1]), float(parts[2])
        samples.append((s_km, px, py))

s_end = samples[-1][0]  # Last s value in km
print(f"Polynomial samples end at s = {s_end} km = {s_end*1000} m")

# Find where QT trace exceeds this
print()
print("Samples near end of polynomial range:")
for i, row in qt0.iterrows():
    dist_km = row['dist_m'] / 1000.0
    if abs(dist_km - s_end) < 0.1:  # Within 100m of end
        print(f"idx={i}, dist={row['dist_m']:.1f}m ({dist_km:.3f}km), px=({row['pixel_x']:.1f}, {row['pixel_y']:.1f})")

# What happens AFTER s_end?
print()
print("="*70)
print("DISTANCE BEHAVIOR AT END OF DEM0 RANGE")
print("="*70)

# Look at last 50 samples of DEM0
print("Last 50 samples of DEM0:")
print("idx    dist_m    pixel_x    pixel_y    in_bounds")
dem_width = 5001  # approximate from ref trace behavior
dem_height = 6001
for i in range(max(0, len(qt0)-50), len(qt0)):
    row = qt0.iloc[i]
    in_bounds = 0 <= row['pixel_x'] < dem_width and 0 <= row['pixel_y'] < dem_height
    print(f"{i:4}  {row['dist_m']:8.2f}  {row['pixel_x']:10.4f}  {row['pixel_y']:10.4f}  {in_bounds}")

# What is the DEM size?
print()
print("="*70)
print("UNDERSTANDING THE TRACE STRUCTURE")
print("="*70)

# The reference trace shows DEM0 goes from 1m to 1121.3m
# But QT trace shows DEM0 going from 1m to 5183.3m
# This suggests QT is NOT exiting DEM0 when it should

# Check if the QT trace has multiple "passes" or segments
qt0['dist_diff'] = qt0['dist_m'].diff()
jumps = qt0[qt0['dist_diff'] < -10]  # Negative distance changes
print(f"Number of negative distance jumps (>10m): {len(jumps)}")
if len(jumps) > 0:
    print("\nNegative jumps:")
    for i, row in jumps.head(10).iterrows():
        print(f"  idx={i}, dist_m={row['dist_m']:.2f}, prev_dist={qt0.iloc[i-1]['dist_m']:.2f}, jump={row['dist_diff']:.2f}")
