import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import argparse
import os

def analyze():
    parser = argparse.ArgumentParser(description='Analyze HorizonGen traces.')
    parser.add_argument('--ref', default='reference_trace.csv', help='Path to reference trace CSV')
    parser.add_argument('--qt', default='quadtree_trace.csv', help='Path to quadtree trace CSV')
    args = parser.parse_args()

    # Load data
    try:
        print(f"Loading Reference: {args.ref}")
        ref = pd.read_csv(args.ref)
        print(f"Loading QuadTree: {args.qt}")
        qt = pd.read_csv(args.qt)
    except FileNotFoundError as e:
        print(f"Error loading CSVs: {e}")
        return

    # Normalize column names
    # Ref: step_idx,dist_km,walker_col,walker_row,caster_x_obs,caster_y_obs,caster_z_obs,slope,max_slope,lat_deg,lon_deg,elevation_m
    # QT: step_index,s_dist,pixel_x,pixel_y,lat_deg,lon_deg,elevation_m,slope,z_local,x_local
    
    # Create common Dist_km
    # Ref might use 'dist_km', 'Dist_km', or 'dist_m' depending on emulator version
    if 'dist_m' in ref.columns:
        ref['Dist_km'] = ref['dist_m'] / 1000.0
    elif 'dist_km' in ref.columns:
        ref['Dist_km'] = ref['dist_km']
    elif 'Dist_km' not in ref.columns and 's_dist' in ref.columns:
         ref['Dist_km'] = ref['s_dist'] / 1000.0

    if 'dist_m' in qt.columns:
        qt['Dist_km'] = qt['dist_m'] / 1000.0
    elif 's_dist' in qt.columns:
        qt['Dist_km'] = qt['s_dist'] / 1000.0
    
    # Normalize pixel columns for comparison
    if 'walker_col' in ref.columns:
        ref['pixel_x_ref'] = ref['walker_col']
        ref['pixel_y_ref'] = ref['walker_row']
    elif 'pixel_x' in ref.columns:
        ref['pixel_x_ref'] = ref['pixel_x']
        ref['pixel_y_ref'] = ref['pixel_y']

    if 'pixel_x' in qt.columns:
        qt['pixel_x_qt'] = qt['pixel_x']
        qt['pixel_y_qt'] = qt['pixel_y']

    # Normalize Elevation and Slope
    # Ref
    if 'elevation_m' in ref.columns:
        ref['elevation_m_ref'] = ref['elevation_m']
    if 'slope' in ref.columns:
        ref['slope_ref'] = ref['slope']
        
    # QT
    if 'elevation_m' in qt.columns:
        qt['elevation_m_qt'] = pd.to_numeric(qt['elevation_m'], errors='coerce')
    if 'slope' in qt.columns:
        qt['slope_qt'] = pd.to_numeric(qt['slope'], errors='coerce')

    # Sort
    ref = ref.sort_values('Dist_km')
    qt = qt.sort_values('Dist_km')

    # Drop NaNs for comparison
    ref_valid = ref.dropna(subset=['elevation_m_ref'])
    qt_valid = qt.dropna(subset=['elevation_m_qt']).copy()

    print(f"Ref points: {len(ref_valid)}")
    print(f"QT points: {len(qt_valid)}")

    # Max Slope Comparison
    ref_max_slope = ref_valid['slope_ref'].max()
    qt_max_slope = qt_valid['slope_qt'].max()
    
    print(f"\nMax Slope Reference: {ref_max_slope:.8f} (Angle: {np.degrees(np.arctan(ref_max_slope)):.4f} deg)")
    print(f"Max Slope QuadTree:  {qt_max_slope:.8f} (Angle: {np.degrees(np.arctan(qt_max_slope)):.4f} deg)")
    
    # Find index of max slope
    ref_max_idx = ref_valid['slope_ref'].idxmax()
    qt_max_idx = qt_valid['slope_qt'].idxmax()
    
    print(f"Ref Max Slope at Dist: {ref_valid.loc[ref_max_idx, 'Dist_km']:.4f} km")
    print(f"QT Max Slope at Dist:  {qt_valid.loc[qt_max_idx, 'Dist_km']:.4f} km")

    # Merge for detailed comparison (Nearest)
    merged = pd.merge_asof(ref_valid, qt_valid, on='Dist_km', direction='nearest', tolerance=0.01) # 10m tolerance

    # Calculate Differences
    merged['Diff_Elev'] = merged['elevation_m_ref'] - merged['elevation_m_qt']
    merged['Diff_Slope'] = merged['slope_ref'] - merged['slope_qt']
    
    # Pixel Distance
    merged['Diff_Px'] = merged['pixel_x_ref'] - merged['pixel_x_qt']
    merged['Diff_Py'] = merged['pixel_y_ref'] - merged['pixel_y_qt']
    merged['Diff_Pos'] = np.sqrt(merged['Diff_Px']**2 + merged['Diff_Py']**2)

    print("\n--- Differences Statistics (Matched Points) ---")
    print(merged[['Diff_Elev', 'Diff_Slope', 'Diff_Pos']].describe())

    # Identify first significant divergence
    # Elevation diff > 1m
    divergence = merged[merged['Diff_Elev'].abs() > 1.0]
    if not divergence.empty:
        first_div = divergence.iloc[0]
        print(f"\nFirst Elevation Divergence (>1m) at {first_div['Dist_km']:.4f} km:")
        print(f"  Ref Elev: {first_div['elevation_m_ref']:.2f}, QT Elev: {first_div['elevation_m_qt']:.2f}, Diff: {first_div['Diff_Elev']:.2f}")
        print(f"  Pos Diff: {first_div['Diff_Pos']:.4f} pixels (dCol: {first_div['Diff_Px']:.4f}, dRow: {first_div['Diff_Py']:.4f})")
    else:
        print("\nNo significant elevation divergence found.")

    # Slope divergence
    slope_div = merged[merged['Diff_Slope'].abs() > 0.001] # approx 0.05 deg
    if not slope_div.empty:
        first_slope = slope_div.iloc[0]
        print(f"\nFirst Slope Divergence (>0.001) at {first_slope['Dist_km']:.4f} km:")
        print(f"  Ref Slope: {first_slope['slope_ref']:.6f}, QT Slope: {first_slope['slope_qt']:.6f}")
        print(f"  Elev Diff at this point: {first_slope['Diff_Elev']:.2f}")

    # Save merged for inspection
    output_base = os.path.dirname(args.qt) if os.path.dirname(args.qt) else '.'
    merge_path = os.path.join(output_base, 'comparison_trace.csv')
    merged.to_csv(merge_path, index=False)
    print(f"\nDetailed comparison saved to {merge_path}")

    # Plotting
    plot_path = os.path.join(output_base, 'trace_comparison.png')
    plt.figure(figsize=(12, 10))
    
    plt.subplot(3, 1, 1)
    plt.plot(ref_valid['Dist_km'], ref_valid['elevation_m_ref'], label='Ref Elev', alpha=0.7)
    plt.plot(qt_valid['Dist_km'], qt_valid['elevation_m_qt'], label='QT Elev', alpha=0.7, linestyle='--')
    plt.ylabel('Elevation (m)')
    plt.legend()
    plt.title('Elevation Profile')

    plt.subplot(3, 1, 2)
    plt.plot(merged['Dist_km'], merged['Diff_Pos'], label='Pixel Position Diff', color='red')
    plt.ylabel('Difference (pixels)')
    plt.legend()
    plt.title('Trace Position Divergence')

    plt.subplot(3, 1, 3)
    plt.plot(ref_valid['Dist_km'], ref_valid['slope_ref'], label='Ref Slope', alpha=0.7)
    plt.plot(qt_valid['Dist_km'], qt_valid['slope_qt'], label='QT Slope', alpha=0.7, linestyle='--')
    plt.ylabel('Slope')
    plt.xlabel('Distance (km)')
    plt.legend()
    plt.title('Slope Comparison')

    plt.tight_layout()
    plt.savefig(plot_path)
    print(f"Plot saved to {plot_path}")

if __name__ == "__main__":
    analyze()
