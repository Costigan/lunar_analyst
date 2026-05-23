import numpy as np
import matplotlib.pyplot as plt

# Simulate the polynomial fitting for both meters and kilometers
def fit_and_evaluate_polynomial(distances_m, pixel_positions, use_kilometers=False):
    """
    Fit a 4th-order polynomial (no constant term) to pixel positions vs distance.
    Returns coefficients and evaluation results.
    """
    if use_kilometers:
        s = distances_m / 1000.0  # Convert to km
        unit_name = "km"
    else:
        s = distances_m
        unit_name = "m"
    
    # Fit polynomial: pixel = a1*s + a2*s^2 + a3*s^3 + a4*s^4
    # Using numpy polyfit (highest degree first, so reverse order)
    # We want no constant term, so we fit through origin
    
    # Create design matrix manually for no-intercept fit
    A = np.column_stack([s, s**2, s**3, s**4])
    coeffs, residuals, rank, s_vals = np.linalg.lstsq(A, pixel_positions, rcond=None)
    
    # Evaluate the polynomial
    pixel_fit = A @ coeffs
    
    # Compute errors
    errors = pixel_positions - pixel_fit
    max_error = np.max(np.abs(errors))
    rms_error = np.sqrt(np.mean(errors**2))
    
    # Check coefficient magnitudes for conditioning
    coeff_magnitudes = np.abs(coeffs)
    
    return {
        'coeffs': coeffs,
        'coeff_magnitudes': coeff_magnitudes,
        'pixel_fit': pixel_fit,
        'errors': errors,
        'max_error': max_error,
        'rms_error': rms_error,
        'unit': unit_name,
        's': s
    }

# Simulate a ray cast across the moon's surface with realistic geometry
def simulate_ray_samples(max_distance_m=100000, moon_radius_m=1737400, azimuth_deg=45):
    """
    Simulate sampling a ray across a curved surface using realistic 3D geometry.
    Projects great circle arc onto stereographic projection to get pixel positions.
    Returns distance in meters and corresponding pixel positions.
    """
    # Observer at south pole (stereographic projection center)
    obs_lat_rad = -np.pi/2
    obs_lon_rad = 0.0
    obs_height_m = 100.0  # 100m above surface
    
    # Observer position in 3D (moon-centered coordinates)
    obs_radius = moon_radius_m + obs_height_m
    obs_vec = np.array([0, 0, -obs_radius])  # At south pole
    
    # Direction vector (azimuth from north)
    azimuth_rad = np.deg2rad(azimuth_deg)
    # In local tangent plane: x=East, y=North, z=Up
    # Transform to global
    dir_local = np.array([np.sin(azimuth_rad), np.cos(azimuth_rad), 0])
    # At south pole: local North = +Y global, local East = +X global
    dir_global = np.array([dir_local[0], dir_local[1], 0])
    dir_global = dir_global / np.linalg.norm(dir_global)
    
    # Sample distances - denser near the observer
    distances_m = np.concatenate([
        np.linspace(2, 100, 40),           # Very near: 2-100m
        np.linspace(100, 1000, 30),        # Near: 100m-1km
        np.linspace(1000, 10000, 30),      # Mid: 1-10km
        np.linspace(10000, max_distance_m, 30)  # Far: 10-100km
    ])
    
    pixel_positions = []
    pixel_size_m = 5.0  # 5 meters per pixel
    
    for dist in distances_m:
        # Point along great circle at distance 'dist' from observer
        # Walk along the surface (great circle)
        angular_dist = dist / moon_radius_m  # radians
        
        # New point on sphere
        point_vec = obs_vec + dir_global * moon_radius_m * np.sin(angular_dist)
        point_vec[2] += moon_radius_m * (np.cos(angular_dist) - 1)  # Adjust for curvature
        
        # Normalize to surface
        point_radius = np.linalg.norm(point_vec)
        point_vec = point_vec / point_radius * moon_radius_m
        
        # Convert to lat/lon
        point_lat = np.arcsin(point_vec[2] / moon_radius_m)
        point_lon = np.arctan2(point_vec[1], point_vec[0])
        
        # Stereographic projection (polar aspect, centered at south pole)
        # Standard formulas for polar stereographic
        k0 = 1.0  # Scale factor at pole
        lat0 = -np.pi/2  # South pole
        
        # For south polar stereographic:
        rho = 2 * k0 * moon_radius_m * np.tan((np.pi/4) + point_lat/2)
        theta = point_lon
        
        x_proj = rho * np.sin(theta)
        y_proj = -rho * np.cos(theta)  # Negative for south pole
        
        # Convert to pixels
        pixel_x = x_proj / pixel_size_m
        pixel_y = y_proj / pixel_size_m
        
        # We'll track pixel distance from origin
        pixel_dist = np.sqrt(pixel_x**2 + pixel_y**2)
        pixel_positions.append(pixel_dist)
    
    return distances_m, np.array(pixel_positions)

# Main analysis
print("=== Polynomial Conditioning Analysis: Meters vs Kilometers ===\n")

# Generate sample data
distances_m, true_pixels = simulate_ray_samples(max_distance_m=100000)

print(f"Sample range: {distances_m[0]:.1f}m to {distances_m[-1]:.1f}m")
print(f"Number of samples: {len(distances_m)}")
print(f"Pixel range: {true_pixels[0]:.2f} to {true_pixels[-1]:.2f}\n")

# Fit with meters
result_m = fit_and_evaluate_polynomial(distances_m, true_pixels, use_kilometers=False)

# Fit with kilometers  
result_km = fit_and_evaluate_polynomial(distances_m, true_pixels, use_kilometers=True)

# Compare results
print("=" * 70)
print("COEFFICIENT MAGNITUDES:")
print("=" * 70)
print(f"{'Term':<10} {'Meters':<20} {'Kilometers':<20} {'Ratio (km/m)':<15}")
print("-" * 70)
for i, (coeff_m, coeff_km) in enumerate(zip(result_m['coeff_magnitudes'], result_km['coeff_magnitudes'])):
    term_name = f"a{i+1}*s^{i+1}"
    ratio = coeff_km / coeff_m if coeff_m > 0 else 0
    print(f"{term_name:<10} {coeff_m:<20.3e} {coeff_km:<20.3e} {ratio:<15.3e}")

print("\n" + "=" * 70)
print("FIT QUALITY:")
print("=" * 70)
print(f"{'Metric':<30} {'Meters':<20} {'Kilometers':<20}")
print("-" * 70)
print(f"{'Max error (pixels)':<30} {result_m['max_error']:<20.6f} {result_km['max_error']:<20.6f}")
print(f"{'RMS error (pixels)':<30} {result_m['rms_error']:<20.6f} {result_km['rms_error']:<20.6f}")

# Check near-field accuracy specifically
near_field_mask = distances_m <= 100
near_m_errors = result_m['errors'][near_field_mask]
near_km_errors = result_km['errors'][near_field_mask]

print(f"\n{'NEAR-FIELD (≤100m) ACCURACY:':<30}")
print(f"{'Max error - meters (pixels)':<30} {np.max(np.abs(near_m_errors)):<20.6f}")
print(f"{'Max error - kilometers (pixels)':<30} {np.max(np.abs(near_km_errors)):<20.6f}")

# Create visualization
fig, axes = plt.subplots(2, 2, figsize=(14, 10))

# Plot 1: Errors vs distance (full range)
ax = axes[0, 0]
ax.plot(distances_m, result_m['errors'], 'b-', label='Meters', alpha=0.7)
ax.plot(distances_m, result_km['errors'], 'r-', label='Kilometers', alpha=0.7)
ax.set_xlabel('Distance (m)')
ax.set_ylabel('Fit Error (pixels)')
ax.set_title('Polynomial Fit Errors: Full Range')
ax.grid(True, alpha=0.3)
ax.legend()
ax.set_xscale('log')

# Plot 2: Errors vs distance (near field only)
ax = axes[0, 1]
ax.plot(distances_m[near_field_mask], near_m_errors, 'b-', label='Meters', alpha=0.7, marker='o')
ax.plot(distances_m[near_field_mask], near_km_errors, 'r-', label='Kilometers', alpha=0.7, marker='s')
ax.set_xlabel('Distance (m)')
ax.set_ylabel('Fit Error (pixels)')
ax.set_title('Polynomial Fit Errors: Near-Field (≤100m)')
ax.grid(True, alpha=0.3)
ax.legend()

# Plot 3: Coefficient magnitudes comparison
ax = axes[1, 0]
terms = ['a1', 'a2', 'a3', 'a4']
x_pos = np.arange(len(terms))
width = 0.35
ax.bar(x_pos - width/2, result_m['coeff_magnitudes'], width, label='Meters', alpha=0.7)
ax.bar(x_pos + width/2, result_km['coeff_magnitudes'], width, label='Kilometers', alpha=0.7)
ax.set_xlabel('Polynomial Term')
ax.set_ylabel('Coefficient Magnitude')
ax.set_title('Coefficient Magnitudes (Log Scale)')
ax.set_yscale('log')
ax.set_xticks(x_pos)
ax.set_xticklabels(terms)
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

# Plot 4: Cumulative error distribution
ax = axes[1, 1]
sorted_errors_m = np.sort(np.abs(result_m['errors']))
sorted_errors_km = np.sort(np.abs(result_km['errors']))
percentiles = np.linspace(0, 100, len(sorted_errors_m))
ax.plot(percentiles, sorted_errors_m, 'b-', label='Meters', alpha=0.7)
ax.plot(percentiles, sorted_errors_km, 'r-', label='Kilometers', alpha=0.7)
ax.set_xlabel('Percentile')
ax.set_ylabel('Absolute Error (pixels)')
ax.set_title('Cumulative Error Distribution')
ax.grid(True, alpha=0.3)
ax.legend()

plt.tight_layout()
plt.savefig('polynomial_accuracy_comparison.png', dpi=150)
print("\n" + "=" * 70)
print("Plot saved to: polynomial_accuracy_comparison.png")
print("=" * 70)

# Test float32 vs float64 for kilometers
print("\n" + "=" * 70)
print("FLOAT32 vs FLOAT64 PRECISION TEST (Kilometers):")
print("=" * 70)

# Refit with float32
s_km_float32 = (distances_m / 1000.0).astype(np.float32)
pixels_float32 = true_pixels.astype(np.float32)
A_float32 = np.column_stack([s_km_float32, s_km_float32**2, s_km_float32**3, s_km_float32**4])
coeffs_float32, _, _, _ = np.linalg.lstsq(A_float32.astype(np.float64), pixels_float32.astype(np.float64), rcond=None)
pixel_fit_float32 = (A_float32 @ coeffs_float32.astype(np.float32)).astype(np.float64)
errors_float32 = true_pixels - pixel_fit_float32

print(f"{'Float64 max error (pixels):':<35} {result_km['max_error']:.6f}")
print(f"{'Float32 max error (pixels):':<35} {np.max(np.abs(errors_float32)):.6f}")
print(f"{'Additional error from float32:':<35} {np.max(np.abs(errors_float32)) - result_km['max_error']:.6f}")

plt.show()
