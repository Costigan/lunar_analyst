import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os

# Load traces
ref_trace_path = r'tests/HorizonGen.Tests/bin/Debug/net9.0/reference_trace.csv'
qt_trace_path = r'tests/HorizonGen.Tests/bin/Debug/net9.0/quadtree_trace.csv'

# Check if files exist
if not os.path.exists(ref_trace_path) or not os.path.exists(qt_trace_path):
    print("Error: Trace files not found.")
    exit()

try:
    df_ref = pd.read_csv(ref_trace_path)
    df_qt = pd.read_csv(qt_trace_path)
except Exception as e:
    print(f"Error reading CSVs: {e}")
    exit()

# Ensure matching lengths for direct comparison (truncate to shorter)
min_len = min(len(df_ref), len(df_qt))
df_ref = df_ref.iloc[:min_len]
df_qt = df_qt.iloc[:min_len]

# Calculate differences
diff_x = df_ref['pixel_x'] - df_qt['pixel_x']
diff_y = df_ref['pixel_y'] - df_qt['pixel_y']
diff_dist = np.sqrt(diff_x**2 + diff_y**2)
diff_elev = df_ref['elevation_m'] - df_qt['elevation_m']
diff_slope = df_ref['slope'] - df_qt['slope']

# Plotting
fig, axs = plt.subplots(3, 1, figsize=(10, 15), sharex=True)

# 1. Pixel Drift
axs[0].plot(df_ref['dist_m'], diff_dist, label='Pixel Distance Diff')
axs[0].set_ylabel('Pixel Difference')
axs[0].set_title('Pixel Coordinate Drift (Ref - QT)')
axs[0].legend()
axs[0].grid(True)

# 2. Elevation Diff
axs[1].plot(df_ref['dist_m'], diff_elev, label='Elevation Diff (m)', color='orange')
axs[1].set_ylabel('Elevation Difference (m)')
axs[1].set_title('Elevation Difference (Ref - QT)')
axs[1].legend()
axs[1].grid(True)

# 3. Slope Diff
axs[2].plot(df_ref['dist_m'], diff_slope, label='Slope Diff', color='green')
axs[2].set_ylabel('Slope Difference')
axs[2].set_xlabel('Distance (m)')
axs[2].set_title('Slope Difference (Ref - QT)')
axs[2].legend()
axs[2].grid(True)

plt.tight_layout()
output_plot = 'analysis/ref_vs_qt_delta.png'
plt.savefig(output_plot)
print(f"Analysis plot saved to {output_plot}")

# Text Summary
print("\n--- Summary Statistics ---")
print(f"Max Pixel Drift: {diff_dist.max():.6f} pixels")
print(f"Max Elevation Diff: {diff_elev.abs().max():.6f} m")
print(f"Max Slope Diff: {diff_slope.abs().max():.6f}")

# Look for large slope jumps
slope_threshold = 0.01
large_diffs = df_ref[diff_slope.abs() > slope_threshold]
if not large_diffs.empty:
    print(f"\nFound {len(large_diffs)} points with slope diff > {slope_threshold}")
    print(large_diffs[['step_index', 'dist_m', 'slope']].head())
else:
    print(f"\nNo points with slope diff > {slope_threshold}")
