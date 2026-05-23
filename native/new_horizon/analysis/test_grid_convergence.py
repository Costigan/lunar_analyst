#!/usr/bin/env python3

import math

def calculate_grid_convergence_magnitude():
    """
    Calculate the magnitude of Grid Convergence corrections needed.
    Based on the documented values from ROTATE_RAYS.md
    """
    
    # From documentation: 0.052° across 128 pixels
    total_change_degrees = 0.052
    tile_width_pixels = 128
    
    # Convert to radians
    total_change_radians = total_change_degrees * math.pi / 180.0
    gradient_rad_per_pixel = total_change_radians / tile_width_pixels
    
    print(f"Grid Convergence Analysis (at ~85°S):")
    print(f"=====================================")
    print(f"Total change across tile: {total_change_degrees:.4f}° ({total_change_radians:.6f} rad)")
    print(f"Gradient: {gradient_rad_per_pixel:.2e} rad/px")
    print(f"Gradient: {gradient_rad_per_pixel * 180.0 / math.pi:.6f}°/px")
    print()
    
    # Maximum corrections (corner pixels are ±64 from center)
    max_offset_pixels = tile_width_pixels // 2  # 64 pixels
    max_correction_rad = gradient_rad_per_pixel * max_offset_pixels
    max_correction_deg = max_correction_rad * 180.0 / math.pi
    
    print(f"Maximum corrections:")
    print(f"  Corner pixels (±{max_offset_pixels} px): ±{max_correction_rad:.6f} rad = ±{max_correction_deg:.4f}°")
    
    # Impact on 100km horizon ray
    horizon_distance_m = 100000
    lateral_shift_m = horizon_distance_m * math.sin(max_correction_rad)
    
    print(f"  At 100km horizon: ±{lateral_shift_m:.1f}m lateral shift")
    
    # Compare to pixel resolution (~20m at this latitude)
    pixel_size_m = 20  # approximate
    shift_in_pixels = lateral_shift_m / pixel_size_m
    
    print(f"  Equivalent to ±{shift_in_pixels:.1f} pixels shift in terrain sampling")
    
    print()
    print("Conclusion:")
    if max_correction_deg > 0.01:  # More than 0.01 degrees
        print(f"❌ Corrections are SIGNIFICANT: {max_correction_deg:.4f}° is substantial for terrain sampling")
    else:
        print(f"✅ Corrections are small: {max_correction_deg:.4f}° might be negligible")
        
    return {
        'gradient_rad_per_pixel': gradient_rad_per_pixel,
        'max_correction_rad': max_correction_rad,
        'max_correction_deg': max_correction_deg,
        'lateral_shift_m': lateral_shift_m
    }

if __name__ == "__main__":
    results = calculate_grid_convergence_magnitude()