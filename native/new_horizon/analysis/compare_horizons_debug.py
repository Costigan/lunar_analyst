import pandas as pd
import numpy as np

files = {
    'ref_4095': 'trace_ref_4095.csv',
    'qt_4095': 'trace_qt_4095.csv',
    'ref_4096': 'trace_ref_4096.csv',
    'qt_4096': 'trace_qt_4096.csv'
}

data = {}
for name, path in files.items():
    try:
        df = pd.read_csv(path)
        # Normalize columns
        col_map = {
            'Slope': 'slope',
            'PixelX': 'pixel_x', 
            'PixelY': 'pixel_y',
            'DistanceMeters': 'dist_m',
            'ElevationMeters': 'elevation_m'
        }
        df = df.rename(columns=col_map)
        
        # Find max slope
        max_slope = df['slope'].max()
        # Convert to degrees (atan)
        max_angle = np.degrees(np.arctan(max_slope))
        data[name] = {'max_slope': max_slope, 'max_angle': max_angle, 'df': df}
        print(f"{name}: Max Slope = {max_slope:.6f}, Max Angle = {max_angle:.4f} deg")
    except Exception as e:
        print(f"Error reading {name}: {e}")

print("-" * 40)
print("Comparisons:")

# Ref vs QT at 4095
if 'ref_4095' in data and 'qt_4095' in data:
    diff = data['qt_4095']['max_angle'] - data['ref_4095']['max_angle']
    print(f"4095 QT vs Ref Diff: {diff:.4f} deg")

# Ref vs QT at 4096
if 'ref_4096' in data and 'qt_4096' in data:
    diff = data['qt_4096']['max_angle'] - data['ref_4096']['max_angle']
    print(f"4096 QT vs Ref Diff: {diff:.4f} deg")

# QT 4095 vs QT 4096
if 'qt_4095' in data and 'qt_4096' in data:
    diff = data['qt_4096']['max_angle'] - data['qt_4095']['max_angle']
    print(f"QT 4096 vs 4095 Diff: {diff:.4f} deg")

# Ref 4095 vs Ref 4096 (Control)
if 'ref_4095' in data and 'ref_4096' in data:
    diff = data['ref_4096']['max_angle'] - data['ref_4095']['max_angle']
    print(f"Ref 4096 vs 4095 Diff: {diff:.4f} deg")

print("-" * 40)
print("Trace Analysis (QT 4095 vs QT 4096)")

if 'qt_4095' in data and 'qt_4096' in data:
    df1 = data['qt_4095']['df']
    df2 = data['qt_4096']['df']
    
    print(f"Step 0 Elev: 4095={df1.iloc[0]['elevation_m']:.2f}, 4096={df2.iloc[0]['elevation_m']:.2f}")

    # Iterate through steps and find where they diverge significantly in slope or sampling behavior
    print(f"Trace lengths: 4095={len(df1)}, 4096={len(df2)}")
    
    min_len = min(len(df1), len(df2))
    for i in range(min_len):
        row1 = df1.iloc[i]
        row2 = df2.iloc[i]
        
        # Check if they are sampling vastly different places relative to start
        # Since start is offset by 1, pixel_x should be offset by ~1. pixel_y should be similar.
        
        dx = row2['pixel_x'] - row1['pixel_x']
        dy = row2['pixel_y'] - row1['pixel_y']
        
        # Expected offset is roughly 1.0 in X, 0.0 in Y
        drift_x = abs(dx - 1.0)
        drift_y = abs(dy)
        
        slope_diff = row2['slope'] - row1['slope']
        
        if abs(slope_diff) > 0.01:
             print(f"Significant slope diff at step {i}:")
             print(f"  4095: dist={row1['dist_m']:.1f}, px={row1['pixel_x']:.1f}, py={row1['pixel_y']:.1f}, slope={row1['slope']:.4f}, state={row1.get('state', '?')}")
             print(f"  4096: dist={row2['dist_m']:.1f}, px={row2['pixel_x']:.1f}, py={row2['pixel_y']:.1f}, slope={row2['slope']:.4f}, state={row2.get('state', '?')}")
             break
