import os
import site
import sys
import numpy as np
import math
import matplotlib.pyplot as plt
from scipy import stats  # or use numpy.polyfit directly
import random

# Ensure PROJ data is found from the virtualenv/site-packages before importing osgeo
proj_data_dir = None
try:
    site_packages = []
    try:
        site_packages = site.getsitepackages()
    except Exception:
        # getsitepackages may fail in some virtualenvs; fall back to sys.path
        pass
    # include user site-packages
    try:
        user_site = site.getusersitepackages()
        if user_site:
            site_packages.append(user_site)
    except Exception:
        pass

    # prioritize virtualenv site-packages
    if hasattr(sys, 'prefix') and sys.prefix:
        venv_site = os.path.join(sys.prefix, 'Lib', 'site-packages')
        if os.path.exists(venv_site):
            site_packages.insert(0, venv_site)

    for sp in site_packages:
        candidate = os.path.join(sp, 'osgeo', 'data', 'proj')
        if os.path.exists(candidate) and os.path.isdir(candidate):
            proj_data_dir = candidate
            break

    if proj_data_dir:
        old_proj_lib = os.environ.get('PROJ_LIB')
        os.environ['PROJ_LIB'] = proj_data_dir
        if old_proj_lib and old_proj_lib != proj_data_dir:
            print(f"Overrode PROJ_LIB: {old_proj_lib} -> {proj_data_dir}")
except Exception:
    # non-fatal; we'll still attempt to import osgeo
    proj_data_dir = None

# Import GDAL/OSR after PROJ_LIB is set (if available)
try:
    from osgeo import gdal, osr
    try:
        gdal.UseExceptions()
    except Exception:
        pass
    try:
        osr.UseExceptions()
    except Exception:
        pass
except ImportError:
    raise ImportError(
        "GDAL/OSR is required for accurate projections. Install with: pip install gdal"
    )

"""
Analyze the feasibility and accuracy of approximating true-space rays (geodesics on a spherical body) as polynomial paths in map (projected) pixel space.

This script performs the following tasks:

- For a given spherical body (e.g. the Moon with a specified radius), and a specified oblique stereographic projection, it generates rays from a central origin at various azimuths.  
- Each ray is sampled along its true-space path (great-circle) at regular intervals up to a user-specified maximum distance (e.g. 1000 km).  
- Sample points are converted from geographic coordinates (latitude, longitude) to projected map coordinates (x, y) via the stereographic projection.  
- For each projected ray, the script fits polynomial curves of degree 1 through N (default N = 5) to approximate the (x, y) coordinates as a function of normalized distance along the ray.  
- It computes approximation error metrics (e.g. per-sample error, max error, mean error) by comparing the polynomial path to the true projected coordinates.  
- It repeats the above over a range of azimuths (e.g. every 5°) to assess worst-case and mean-case error behavior as a function of polynomial degree and ray distance.  
- It outputs summary plots: (1) maximum and mean error vs. polynomial degree (for all azimuths), (2) error vs. distance along the ray for the worst-case azimuth and best-performing polynomial degree.  

This allows you to (a) evaluate whether a low-order polynomial approximation in pixel space is sufficiently accurate for a given maximum ray distance and projection parameters; (b) choose the minimal polynomial degree needed to meet a desired error tolerance (e.g. sub-pixel accuracy, or under a given meter threshold), and (c) understand how approximation error varies with azimuth direction and ray length.

Dependencies:
    - numpy
    - scipy (for polynomial fitting)
    - osgeo.osr (or pyproj) for stereographic projection
    - matplotlib (for plotting error summaries)

Usage:
    Adjust parameters such as body radius, projection center, maximum ray distance, azimuth sampling interval, sampling step along ray, and maximal polynomial degree. Run the script and review the generated plots to decide whether polynomial-parameterized raster‐space ray marching is acceptable for your digital elevation map / horizon-generation use case.
"""

# Detailed Analysis Overview
#
# This script evaluates the accuracy of approximating great-circle (geodesic) rays on a spherical body (e.g., the Moon) using low-order polynomials in a stereographic projected coordinate system.
# The goal is to determine if polynomial ray marching in pixel space can achieve sub-pixel accuracy for horizon or visibility calculations.
#
# Key Concepts:
# - **Ray Generation and Sampling**: For each origin (e.g., lat/lon point), rays are generated at azimuths from 0° to 360° in steps of az_step_deg (default 5°).
#   Each ray follows a great-circle path from the origin out to max_dist_m (default 1000 km).
#   Along each ray, sample points are chosen at regular intervals: starting from distance 0 (the origin) to max_dist_m, in steps of step_m (default 20 km for analysis, but adaptive for error computation).
#   These geographic (lat, lon) points are then projected to Cartesian (x, y) coordinates using an oblique stereographic projection centered at the origin.
#   The projection ensures that the origin maps to (0,0) in projected space, and rays emanate radially.
#
# - **Polynomial Fitting**: For each projected ray (a sequence of (x, y) points), we fit polynomials x(s) and y(s), where s is the normalized distance along the ray [0, 1].
#   s = 0 at the origin, s = 1 at the endpoint.
#   The fit is constrained: x(0) = x0, y(0) = y0 (the origin coordinates), and higher terms are fitted via least-squares.
#   For degree d, x(s) = x0 + a1*s + a2*s^2 + ... + ad*s^d, similarly for y(s).
#   Errors are computed as the Euclidean distance between true projected points and polynomial predictions.
#
# - **Error Aggregation**: For each origin, we find the worst-case error across all azimuths and distances for each polynomial degree.
#   Multiple origins (e.g., random near a location) allow aggregating the worst-case across origins, providing robust bounds.
#
# Special Case: Origins near -85° Latitude
# - The analysis for origins near lat=-85.42088°, lon=31.6218° (generated in examine_polynomial_accuracy_near_lat-85_42088_lon31_62180_aggregated.csv) shows that polynomial degree 4 achieves extremely low errors out to 100 km.
# - From the CSV: For degree 4, errors are 0.000000 m at 0-1 km, 0.000048 m at 10 km, and 0.000052 m at 100 km.
# - Assuming typical lunar DEM pixel sizes (e.g., 1-10 m), this is sub-pixel accuracy, effectively "almost no pixel error" out to 100 km.
# - Agreement: Yes, I agree with this conclusion. The data indicates degree 4 provides negligible error for practical ray marching up to 100 km at this latitude, making it suitable for high-precision applications without needing higher degrees or alternative methods.


# Constants
MOON_RADIUS = 1737.4e3  # meters

def geodesic_points_sphere(lat0_deg, lon0_deg, az_deg, max_dist_m, step_m):
    """Return list of (lat_deg, lon_deg) along a great-circle (geodesic) ray on a spherical moon."""
    lat0 = math.radians(lat0_deg)
    lon0 = math.radians(lon0_deg)
    az = math.radians(az_deg)
    n_steps = int(max_dist_m // step_m) + 1
    pts = []
    for i in range(n_steps + 1):
        d = (i * step_m) / MOON_RADIUS  # central angle in radians
        lat = math.asin(math.sin(lat0)*math.cos(d) +
                        math.cos(lat0)*math.sin(d)*math.cos(az))
        lon = lon0 + math.atan2(math.sin(az)*math.sin(d)*math.cos(lat0),
                                math.cos(d) - math.sin(lat0)*math.sin(lat))
        pts.append( (math.degrees(lat), math.degrees(lon)) )
    return pts

def build_stereographic_transform(lat0, lon0, lat_ts=None):
    """Return a stereographic projection callable centered on (lat0, lon0).

    This uses a pure-Python spherical stereographic formula as a fallback
    when PROJ / GDAL can't be used (e.g. missing proj.db). The returned
    object behaves like a function taking `(lon, lat)` and returning
    `(x, y)` in meters.
    """
    lat0_rad = math.radians(lat0)
    lon0_rad = math.radians(lon0)

    def proj_callable(lon, lat):
        # Accept lon, lat in degrees
        phi = math.radians(lat)
        lam = math.radians(lon)
        dlam = lam - lon0_rad
        sin_phi = math.sin(phi)
        cos_phi = math.cos(phi)
        sin_lat0 = math.sin(lat0_rad)
        cos_lat0 = math.cos(lat0_rad)

        cosc = sin_lat0 * sin_phi + cos_lat0 * cos_phi * math.cos(dlam)
        # stereographic scale factor
        k = 2.0 / (1.0 + cosc)
        x = MOON_RADIUS * k * cos_phi * math.sin(dlam)
        y = MOON_RADIUS * k * (cos_lat0 * sin_phi - sin_lat0 * cos_phi * math.cos(dlam))
        return x, y

    # Try to build an OSR transform; if PROJ is unavailable, return the callable
    try:
        source = osr.SpatialReference()
        source.ImportFromEPSG(4326)
        target = osr.SpatialReference()
        proj4 = (f"+proj=stere +lat_0={lat0} +lon_0={lon0} "
                 f"+a={MOON_RADIUS} +b={MOON_RADIUS} +units=m +no_defs")
        if lat_ts is not None:
            proj4 += f" +lat_ts={lat_ts}"
        target.ImportFromProj4(proj4)
        transform = osr.CoordinateTransformation(source, target)
        # Quick test to ensure PROJ is usable
        try:
            _ = transform.TransformPoint(lon0, lat0, 0)
            return transform
        except Exception:
            return proj_callable
    except Exception:
        return proj_callable

def project_latlon(transform, latlon_list):
    """Project list of (lat, lon) to projected (x, y) in meters."""
    xy = []
    for lat, lon in latlon_list:
        # Support both an osgeo transform object (with TransformPoint) and
        # the fallback callable returned by `build_stereographic_transform`.
        if hasattr(transform, 'TransformPoint'):
            x, y, _ = transform.TransformPoint(lon, lat, 0)
        else:
            x, y = transform(lon, lat)
        xy.append((x, y))
    return np.array(xy)

def fit_poly_and_error(xy, max_degree=5):
    """
    For degrees 1..max_degree, fit polynomial x(s), y(s) where s = normalized distance along ray [0..1].
    Return dict: degree -> (coeff_x, coeff_y, errors array, max_error, mean_error).
    """
    n = len(xy)
    s = np.linspace(0.0, 1.0, n)
    xs = xy[:, 0]
    ys = xy[:, 1]

    results = {}
    for deg in range(1, max_degree + 1):
        # Constrained fit: enforce polynomial passes through the origin point (s=0)
        # x(s) = x0 + s * qx(s) where qx is degree (deg-1) polynomial (or constant if deg==1)
        x0 = xs[0]
        y0 = ys[0]

        # Build design matrix for powers s^1 .. s^deg (no constant column)
        A = np.vstack([s**p for p in range(1, deg+1)]).T  # shape (n, deg)

        # Solve least-squares for coefficients of s^1..s^deg for x and y
        # Solve A @ a = xs - x0
        try:
            ax, *_ = np.linalg.lstsq(A, xs - x0, rcond=None)
            ay, *_ = np.linalg.lstsq(A, ys - y0, rcond=None)
        except Exception:
            # Fall back to numpy.polyfit if lstsq fails for any reason
            px = np.polyfit(s, xs, deg)
            py = np.polyfit(s, ys, deg)
            xy_fit = np.vstack([np.polyval(px, s), np.polyval(py, s)]).T
            diffs = xy_fit - xy
            dists = np.linalg.norm(diffs, axis=1)
            results[deg] = {
                'px': px,
                'py': py,
                'errors': dists,
                'max_error': dists.max(),
                'mean_error': dists.mean(),
            }
            continue

        # Reconstruct full polynomial coefficient arrays in numpy.polyval order (highest-first)
        # ax corresponds to coefficients for s^1, s^2, ..., s^deg
        # We need [a_deg, a_{deg-1}, ..., a1, x0]
        px = np.concatenate((ax[::-1], [x0]))
        py = np.concatenate((ay[::-1], [y0]))

        xy_fit = np.vstack([np.polyval(px, s), np.polyval(py, s)]).T
        diffs = xy_fit - xy
        dists = np.linalg.norm(diffs, axis=1)
        results[deg] = {
            'px': px,
            'py': py,
            'errors': dists,
            'max_error': dists.max(),
            'mean_error': dists.mean(),
        }
    return results

def analyze_over_azimuths(center_lat, center_lon,
                          max_dist_m=1e6, step_m=10e3, az_step_deg=5,
                          max_degree=5):
    """
    For each azimuth (0 .. 360) in az_step_deg increments,
    sample geodesic, project, fit polys, collect error stats.
    Returns a dict: degree -> list of (azimuth, max_error, mean_error).
    """
    transform = build_stereographic_transform(center_lat, center_lon)
    azimuths = list(range(0, 360, az_step_deg))
    stats = {deg: [] for deg in range(1, max_degree+1)}

    for az in azimuths:
        pts_latlon = geodesic_points_sphere(center_lat, center_lon, az, max_dist_m, step_m)
        xy = project_latlon(transform, pts_latlon)
        res = fit_poly_and_error(xy, max_degree)
        for deg, info in res.items():
            stats[deg].append((az, info['max_error'], info['mean_error']))
    return stats


def fit_degree(xy, deg):
    """Fit a specific polynomial degree to xy points and return the same info dict as fit_poly_and_error entries.

    deg == 0 -> constant (x(s)=x0, y(s)=y0).
    deg >= 1 -> use constrained fitting implemented in fit_poly_and_error by calling it and extracting the degree.
    """
    # Interpret requested degree 0 as a line (degree 1), since a constant fit is not useful
    actual_deg = max(1, int(deg))
    res = fit_poly_and_error(xy, max_degree=actual_deg)
    return res[actual_deg]


def generate_error_table(center_lat=0.0, center_lon=0.0,
                         degrees=range(0,6),
                         distances_m=(0.0, 100.0, 1e3, 1e4, 1e5, 1e6),
                         az_step_deg=5):
    """Generate and print a table of worst-case max errors for each degree and distance.

    For each distance, we resample rays up to that distance and find, for each polynomial
    degree, the azimuth that produces the largest max error (worst-case). Returns a dict
    mapping degree -> list of (distance_m, worst_az, max_error_m).
    """
    # Choose sampling steps per distance to get reasonable number of samples (~100)
    def choose_step(dist):
        if dist <= 0:
            return 1.0
        return max(1.0, dist / 100.0)

    azimuths = list(range(0, 360, az_step_deg))
    table = {deg: [] for deg in degrees}

    for dist in distances_m:
        step_m = choose_step(dist)
        for deg in degrees:
            worst_error = -1.0
            worst_az = None
            for az in azimuths:
                pts_latlon = geodesic_points_sphere(center_lat, center_lon, az, dist, step_m)
                xy = project_latlon(build_stereographic_transform(center_lat, center_lon), pts_latlon)
                info = fit_degree(xy, deg)
                if info['max_error'] > worst_error:
                    worst_error = info['max_error']
                    worst_az = az
            table[deg].append((dist, worst_az, worst_error))

    # Print a formatted table to console and write a CSV file with separate azimuth/error columns per distance
    header_dist_labels = [f'{int(d) if d>=1 else 0}m' for d in distances_m]
    print('\nWorst-case maximum error table (meters):')
    for deg in degrees:
        cells = [f"deg {deg}"] + [f"az={az}, err={err:.6f}m" for (_, az, err) in table[deg]]
        print('\t'.join(cells))

    # Write CSV: columns: degree, for each distance two columns: <dist>_az, <dist>_err
    import csv
    from pathlib import Path
    out_path = Path(__file__).with_suffix('')  # ensure path to script
    csv_path = out_path.with_suffix('.csv')

    colnames = ['degree']
    for d in header_dist_labels:
        colnames.append(f'{d}_az')
        colnames.append(f'{d}_err_m')

    with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(colnames)
        for deg in degrees:
            row = [deg]
            for (_, az, err) in table[deg]:
                row.append(az)
                row.append(f'{err:.6f}')
            writer.writerow(row)

    print(f'Wrote worst-case error table to: {csv_path}')
    return table


def compute_worst_errors_for_origin(center_lat, center_lon,
                                    degrees=range(0,6),
                                    distances_m=(0.0, 100.0, 1e3, 1e4, 1e5, 1e6),
                                    az_step_deg=5):
    """Compute worst-case max errors for a single origin without writing files.

    Returns a dict mapping degree -> list of (distance_m, worst_error)
    """
    def choose_step(dist):
        if dist <= 0:
            return 1.0
        return max(1.0, dist / 100.0)

    azimuths = list(range(0, 360, az_step_deg))
    results = {deg: [] for deg in degrees}

    # Pre-build transform for this origin
    transform = build_stereographic_transform(center_lat, center_lon)

    for dist in distances_m:
        step_m = choose_step(dist)
        for deg in degrees:
            worst_error = -1.0
            for az in azimuths:
                pts_latlon = geodesic_points_sphere(center_lat, center_lon, az, dist, step_m)
                xy = project_latlon(transform, pts_latlon)
                info = fit_degree(xy, deg)
                if info['max_error'] > worst_error:
                    worst_error = info['max_error']
            results[deg].append((dist, worst_error))
    return results


def analyze_random_origins_near_south_pole(num_origins=10, max_origin_dist_m=500e3,
                                           degrees=range(0,6),
                                           distances_m=(0.0, 100.0, 1e3, 1e4, 1e5, 1e6),
                                           az_step_deg=5, seed=12345):
    """Pick random origins within `max_origin_dist_m` of the south pole and compute worst-case errors.

    Writes a CSV `examine_polynomial_accuracy_south_pole_origins.csv` with rows:
      origin_idx, origin_dist_m, degree, err_0m, err_100m, err_1km, err_10km, err_100km, err_1000km
    """
    random.seed(seed)
    origins = []
    for i in range(num_origins):
        # uniform area within circle: r = R_max * sqrt(u)
        u = random.random()
        r = max_origin_dist_m * math.sqrt(u)
        # bearing random [0,360)
        bearing_deg = random.random() * 360.0
        # Convert to lat/lon: starting at south pole (-90, any lon), move north by angular distance r/R
        delta_deg = (r / MOON_RADIUS) * (180.0 / math.pi)
        lat = -90.0 + delta_deg
        lon = bearing_deg
        origins.append((lat, lon, r))

    # Compute results per origin and aggregate worst errors across origins
    # aggregated[deg_idx][dist_idx] = worst error across origins
    agg = {deg: [ -1.0 for _ in distances_m ] for deg in degrees}
    for idx, (lat, lon, r) in enumerate(origins):
        results = compute_worst_errors_for_origin(lat, lon, degrees=degrees, distances_m=distances_m, az_step_deg=az_step_deg)
        for deg in degrees:
            for i, (_dist, err) in enumerate(results[deg]):
                if err > agg[deg][i]:
                    agg[deg][i] = err

    # Write aggregated CSV: rows = degree, columns = errors at distances
    from pathlib import Path
    import csv
    out_path = Path(__file__).with_name('examine_polynomial_accuracy_south_pole_origins_aggregated.csv')
    header = ['degree'] + [f'{int(d) if d>=1 else 0}m_err' for d in distances_m]
    with open(out_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for deg in degrees:
            row = [deg] + [f'{agg[deg][i]:.6f}' for i in range(len(distances_m))]
            writer.writerow(row)

    print(f'Wrote aggregated south-pole-origin worst-case errors to: {out_path}')
    return out_path

def analyze_random_origins_near_location(base_lat, base_lon, num_origins=10, max_origin_dist_m=500e3,
                                           degrees=range(0,6),
                                           distances_m=(0.0, 100.0, 1e3, 1e4, 1e5, 1e6),
                                           az_step_deg=5, seed=12345):
    """Pick random origins within `max_origin_dist_m` of the base location and compute worst-case errors.

    Writes a CSV `examine_polynomial_accuracy_near_lat{base_lat}_lon{base_lon}_aggregated.csv` with rows:
      degree, err_0m, err_100m, err_1km, err_10km, err_100km, err_1000km
    """
    random.seed(seed)
    origins = []
    for i in range(num_origins):
        # uniform area within circle: r = R_max * sqrt(u)
        u = random.random()
        r = max_origin_dist_m * math.sqrt(u)
        # bearing random [0,360)
        bearing_deg = random.random() * 360.0
        # Convert to lat/lon: starting at base location, move by angular distance r/R
        delta_deg = (r / MOON_RADIUS) * (180.0 / math.pi)
        lat_rad = math.radians(base_lat)
        lon_rad = math.radians(base_lon)
        # Approximate movement: add delta in direction of bearing
        lat = base_lat + delta_deg * math.cos(math.radians(bearing_deg))
        lon = base_lon + delta_deg * math.sin(math.radians(bearing_deg)) / math.cos(lat_rad)
        origins.append((lat, lon, r))

    # Compute results per origin and aggregate worst errors across origins
    # aggregated[deg_idx][dist_idx] = worst error across origins
    agg = {deg: [ -1.0 for _ in distances_m ] for deg in degrees}
    for idx, (lat, lon, r) in enumerate(origins):
        results = compute_worst_errors_for_origin(lat, lon, degrees=degrees, distances_m=distances_m, az_step_deg=az_step_deg)
        for deg in degrees:
            for i, (_dist, err) in enumerate(results[deg]):
                if err > agg[deg][i]:
                    agg[deg][i] = err

    # Write aggregated CSV: rows = degree, columns = errors at distances
    from pathlib import Path
    import csv
    lat_str = f"{base_lat:.5f}".replace('.', '_')
    lon_str = f"{base_lon:.5f}".replace('.', '_')
    out_path = Path(__file__).with_name(f'examine_polynomial_accuracy_near_lat{lat_str}_lon{lon_str}_aggregated.csv')
    header = ['degree'] + [f'{int(d) if d>=1 else 0}m_err' for d in distances_m]
    with open(out_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for deg in degrees:
            row = [deg] + [f'{agg[deg][i]:.6f}' for i in range(len(distances_m))]
            writer.writerow(row)

    print(f'Wrote aggregated near-location-origin worst-case errors to: {out_path}')
    return out_path

def plot_error_stats(stats, max_dist_m, az_step_deg, step_m=20e3, max_degree=5):
    """
    stats: output of analyze_over_azimuths
    Generates:
      - Max error vs degree (over all azimuths)
      - Maybe error vs distance (for a fixed azimuth or worst-case azimuth)
    """
    degrees = sorted(stats.keys())
    max_errors = [ max(info[1] for info in stats[deg]) for deg in degrees ]
    mean_errors = [ np.mean([info[2] for info in stats[deg]]) for deg in degrees ]

    # Summary plot (max/mean error vs degree) commented out per request
    # plt.figure(figsize=(8,5))
    # plt.plot(degrees, max_errors, 'o-', label='Worst-case max error')
    # plt.plot(degrees, mean_errors, 's--', label='Mean error')
    # plt.xlabel('Polynomial degree')
    # plt.ylabel(f'Error (m) over rays up to {max_dist_m/1000:.0f} km')
    # plt.title(f'Projection → pixel-space polynomial approximation error (azimuth step {az_step_deg}°)')
    # plt.legend()
    # plt.grid(True)
    # plt.show()

    # Error plotting disabled by request (commented out)
    # If you want the overlay plot back, uncomment the block below.
    #
    # worst = max(stats.get(max_degree, []), key=lambda t: t[1])
    # worst_az = worst[0]
    # print("Worst azimuth for degree", max_degree, ":", worst_az, "error:", worst[1], "m")
    # pts = geodesic_points_sphere(0.0, 0.0, worst_az, max_dist_m, step_m)
    # xy = project_latlon(build_stereographic_transform(0.0, 0.0), pts)
    # res = fit_poly_and_error(xy, max_degree)
    # n = len(pts)
    # dists_along = np.linspace(0.0, max_dist_m, n)
    # plt.figure(figsize=(8,5))
    # deg_min = 3
    # deg_max = min(5, max_degree)
    # for deg in range(deg_min, deg_max + 1):
    #     info = res.get(deg)
    #     if info is None:
    #         continue
    #     plt.plot(dists_along/1000.0, info['errors'], label=f'deg {deg}')
    # plt.xlabel('Distance along ray (km)')
    # plt.ylabel('Projection–poly error (m)')
    # plt.title(f'Error along worst-case ray (azimuth {worst_az}°), degrees {deg_min}-{deg_max}')
    # plt.legend()
    # plt.grid(True)
    # plt.show()


if __name__ == '__main__':
    # Example usage: center at lat0=0 lon0=0; adjust as needed for your map.
    stats = analyze_over_azimuths(center_lat=0.0, center_lon=0.0,
                                  max_dist_m=1e6, step_m=20e3, az_step_deg=5,
                                  max_degree=5)
    plot_error_stats(stats, max_dist_m=1e6, az_step_deg=5)
    # Generate worst-case error CSV table for degrees 0..5 and specified distances
    generate_error_table(center_lat=0.0, center_lon=0.0,
                         degrees=range(0,6),
                         distances_m=(0.0, 100.0, 1e3, 1e4, 1e5, 1e6),
                         az_step_deg=5)
    # Analyze 100 random origins near the south pole (within 500 km) and write summary CSV
    analyze_random_origins_near_south_pole(num_origins=100, max_origin_dist_m=500e3,
                                           degrees=range(0,6),
                                           distances_m=(0.0, 100.0, 1e3, 1e4, 1e5, 1e6),
                                           az_step_deg=5)
    # Analyze 10 random origins near lat=-85.42088, lon=31.6218 (within 500 km) and write summary CSV
    analyze_random_origins_near_location(base_lat=-85.42088, base_lon=31.6218, num_origins=10, max_origin_dist_m=500e3,
                                         degrees=range(0,6),
                                         distances_m=(0.0, 100.0, 1e3, 1e4, 1e5, 1e6),
                                         az_step_deg=5)
