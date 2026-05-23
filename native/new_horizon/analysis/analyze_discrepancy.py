"""
Analyze the discrepancy between Reference and QuadTree emulator traces.
Looking for where the 90° horizon angle comes from.
"""

import pandas as pd
import numpy as np
import os

output_dir = r"/d/projects/new_horizon/output_debug"

# Load traces
ref_traces = []
qt_traces = []

for i in range(3):
    ref_path = os.path.join(output_dir, f"diag_ref_dem{i}.csv")
    qt_path = os.path.join(output_dir, f"diag_qt_dem{i}.csv")
    
    if os.path.exists(ref_path):
        ref_traces.append(pd.read_csv(ref_path))
        print(f"Loaded ref DEM{i}: {len(ref_traces[-1])} samples")
    if os.path.exists(qt_path):
        qt_traces.append(pd.read_csv(qt_path))
        print(f"Loaded QT DEM{i}: {len(qt_traces[-1])} samples")

print("\n" + "="*60)
print("ANALYSIS: Finding maximum slopes")
print("="*60)

# Analyze each DEM's trace
for i, (ref, qt) in enumerate(zip(ref_traces, qt_traces)):
    print(f"\n--- DEM {i} ---")
    
    # Reference
    ref_max_slope = ref['Slope'].max()
    ref_max_idx = ref['Slope'].idxmax()
    ref_max_row = ref.loc[ref_max_idx]
    ref_angle = np.degrees(np.arctan(ref_max_slope))
    print(f"Ref: max_slope={ref_max_slope:.6f} => {ref_angle:.4f}° at dist={ref_max_row['DistanceMeters']:.1f}m")
    
    # QuadTree
    qt_max_slope = qt['slope'].max()
    qt_max_idx = qt['slope'].idxmax()
    qt_max_row = qt.loc[qt_max_idx]
    qt_angle = np.degrees(np.arctan(qt_max_slope))
    print(f"QT:  max_slope={qt_max_slope:.6f} => {qt_angle:.4f}° at dist={qt_max_row['dist_m']:.1f}m")
    
    # Check for extreme values
    extreme = qt[qt['slope'] > 10]  # Slope > 10 would be > 84°
    if len(extreme) > 0:
        print(f"\n*** EXTREME SLOPES FOUND in QT trace: {len(extreme)} samples ***")
        print(extreme[['dist_m', 'pixel_x', 'pixel_y', 'elevation_m', 'slope']].head(10))

print("\n" + "="*60)
print("COMBINED ANALYSIS")
print("="*60)

# Combine all traces
all_ref_slopes = np.concatenate([t['Slope'].values for t in ref_traces])
all_qt_slopes = np.concatenate([t['slope'].values for t in qt_traces])

ref_combined_max = all_ref_slopes.max()
qt_combined_max = all_qt_slopes.max()

print(f"Reference combined max slope: {ref_combined_max:.6f} => {np.degrees(np.arctan(ref_combined_max)):.4f}°")
print(f"QuadTree combined max slope:  {qt_combined_max:.6f} => {np.degrees(np.arctan(qt_combined_max)):.4f}°")

# Check if QT has any NaN or Inf
qt_nan = sum(np.isnan(all_qt_slopes))
qt_inf = sum(np.isinf(all_qt_slopes))
print(f"\nQT trace has {qt_nan} NaN values and {qt_inf} Inf values")

# Look at where slopes diverge
print("\n" + "="*60)
print("COMPARING FIRST 50 SAMPLES (DEM0)")
print("="*60)

ref0 = ref_traces[0]
qt0 = qt_traces[0]

# Align by distance (approximately)
print("\nDist(m)    Ref_Slope    QT_Slope     Diff       Ref_Elev    QT_Elev")
for i in range(min(30, len(ref0), len(qt0))):
    r = ref0.iloc[i]
    q = qt0.iloc[i]
    diff = q['slope'] - r['Slope']
    flag = " ***" if abs(diff) > 0.01 else ""
    print(f"{r['DistanceMeters']:8.1f}  {r['Slope']:11.6f}  {q['slope']:11.6f}  {diff:+9.6f}  {r['ElevationMeters']:10.2f}  {q['elevation_m']:10.2f}{flag}")

# Check the polynomial samples
print("\n" + "="*60)
print("POLYNOMIAL SAMPLES (QT DEM0)")
print("="*60)

samples_path = os.path.join(output_dir, "diag_qt_dem0.samples.txt")
if os.path.exists(samples_path):
    with open(samples_path) as f:
        lines = f.readlines()
    print(f"Number of polynomial samples: {len(lines)}")
    print("\nFirst 10 samples (s_km : px : py):")
    for line in lines[:10]:
        parts = line.strip().split(':')
        if len(parts) == 3:
            s_km, px, py = float(parts[0]), float(parts[1]), float(parts[2])
            print(f"  s={s_km*1000:8.1f}m  px={px:10.4f}  py={py:10.4f}")
    print("\nLast 5 samples:")
    for line in lines[-5:]:
        parts = line.strip().split(':')
        if len(parts) == 3:
            s_km, px, py = float(parts[0]), float(parts[1]), float(parts[2])
            print(f"  s={s_km*1000:8.1f}m  px={px:10.4f}  py={py:10.4f}")
else:
    print("Samples file not found")

# Check distance ranges
print("\n" + "="*60)
print("DISTANCE RANGES")
print("="*60)

for i, (ref, qt) in enumerate(zip(ref_traces, qt_traces)):
    print(f"DEM{i}:")
    print(f"  Ref: {ref['DistanceMeters'].min():.1f}m to {ref['DistanceMeters'].max():.1f}m ({len(ref)} samples)")
    print(f"  QT:  {qt['dist_m'].min():.1f}m to {qt['dist_m'].max():.1f}m ({len(qt)} samples)")
