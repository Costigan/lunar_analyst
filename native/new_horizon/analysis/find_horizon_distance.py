import pandas as pd
import numpy as np

ref_trace_path = r'tests\HorizonGen.Tests\bin\Debug\net9.0\reference_trace.csv'
qt_trace_path = r'tests\HorizonGen.Tests\bin\Debug\net9.0\quadtree_trace.csv'

try:
    df_ref = pd.read_csv(ref_trace_path)
    df_qt = pd.read_csv(qt_trace_path)
except Exception as e:
    print(f"Error reading CSVs: {e}")
    exit()

# Target slopes (tangent of the reported degrees)
# refDeg=-5.272646 => tan(-5.272646 deg) = -0.092286
# qtDeg=-4.980641 => tan(-4.980641 deg) = -0.087149

target_ref_slope = np.tan(np.deg2rad(-5.272646))
target_qt_slope = np.tan(np.deg2rad(-4.980641))

print(f"Target Ref Slope: {target_ref_slope}")
print(f"Target QT Slope: {target_qt_slope}")

# Find row with closest slope
# The horizon is the MAX slope encountered. So we look for where the slope equals the max.

# Ref
max_ref_slope = df_ref['slope'].max()
ref_row = df_ref[df_ref['slope'] == max_ref_slope].iloc[0]
print(f"\n--- Reference Horizon Point ---")
print(f"Distance: {ref_row['dist_m']:.2f} m")
print(f"Slope: {ref_row['slope']:.6f}")
print(f"Elevation: {ref_row['elevation_m']:.2f}")

# QT
# Note: QT trace might have NaNs or different structure, max slope is what we want.
max_qt_slope = df_qt['slope'].max()
qt_row = df_qt[df_qt['slope'] == max_qt_slope].iloc[0]
print(f"\n--- QuadTree Horizon Point ---")
print(f"Distance: {qt_row['dist_m']:.2f} m")
print(f"Slope: {qt_row['slope']:.6f}")
print(f"Elevation: {qt_row['elevation_m']:.2f}")
