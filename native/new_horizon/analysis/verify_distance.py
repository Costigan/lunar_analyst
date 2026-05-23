"""
Verify the tangent-distance-to-pixel relationship.
Walk 1000m in 3D space and see how many pixels we move.
"""

import numpy as np

# Moon radius in meters
R = 1737400.0

# Observer at lat=-85.4°, lon=32.2° (from our test case)
obs_lat_deg = -85.426
obs_lon_deg = 32.211
obs_lat_rad = np.radians(obs_lat_deg)
obs_lon_rad = np.radians(obs_lon_deg)

# Observer height (terrain + offset)
obs_height = 6359 + 0  # meters above datum

# Observer position in ME (Moon-centered) frame
def lat_lon_to_me(lat_rad, lon_rad, radius):
    cos_lat = np.cos(lat_rad)
    sin_lat = np.sin(lat_rad)
    cos_lon = np.cos(lon_rad)
    sin_lon = np.sin(lon_rad)
    return np.array([
        radius * cos_lat * cos_lon,
        radius * cos_lat * sin_lon,
        radius * sin_lat
    ])

obs_radius = R + obs_height
obs_vec = lat_lon_to_me(obs_lat_rad, obs_lon_rad, obs_radius)
print(f"Observer vector magnitude: {np.linalg.norm(obs_vec):.1f} m")
print(f"Expected (R + height): {obs_radius:.1f} m")

# Azimuth 51.75° - direction in observer frame
azimuth_deg = 51.75
az_rad = np.radians(azimuth_deg)

# In observer frame: X=East, Y=North, Z=Up
# Azimuth is clockwise from North
# So direction = (sin(az), cos(az), 0) in ENU frame
# But the code uses angle = π/2 - az, then (cos(angle), sin(angle), 0)
angle = np.pi/2 - az_rad
dir_obs = np.array([np.cos(angle), np.sin(angle), 0])
print(f"\nDirection in observer frame: {dir_obs}")

# Transform to ME frame using rotation matrix
# The rotation matrix transforms from observer frame to ME frame
def get_rotation_matrix(lat_rad, lon_rad):
    """Get rotation matrix from ME to Observer frame (matches code's meToObs)"""
    # Z-axis rotation by -lon
    c1, s1 = np.cos(-lon_rad), np.sin(-lon_rad)
    R1 = np.array([[c1, -s1, 0], [s1, c1, 0], [0, 0, 1]])
    
    # Y-axis rotation by -(π/2 - lat)
    angle2 = -(np.pi/2 - lat_rad)
    c2, s2 = np.cos(angle2), np.sin(angle2)
    R2 = np.array([[c2, 0, s2], [0, 1, 0], [-s2, 0, c2]])
    
    # Z-axis rotation by -π/2 (fixEnu)
    c3, s3 = np.cos(-np.pi/2), np.sin(-np.pi/2)
    R3 = np.array([[c3, -s3, 0], [s3, c3, 0], [0, 0, 1]])
    
    return R1 @ R2 @ R3

me_to_obs = get_rotation_matrix(obs_lat_rad, obs_lon_rad)
obs_to_me = np.linalg.inv(me_to_obs)

dir_me = obs_to_me @ dir_obs
dir_me = dir_me / np.linalg.norm(dir_me)
print(f"Direction in ME frame: {dir_me}")

# Now walk distances and see where we end up
print("\n" + "="*70)
print("TANGENT DISTANCE TO PIXEL MAPPING")
print("="*70)

# DEM0 has resolution of ~5 m/pixel
map_res = 4.99  # m/pixel

def me_to_lat_lon(vec):
    """Convert ME vector to lat/lon in radians"""
    lon = np.arctan2(vec[1], vec[0])
    if lon < 0:
        lon += 2 * np.pi
    alen = np.sqrt(vec[0]**2 + vec[1]**2)
    lat = np.arctan2(vec[2], alen)
    return lat, lon

# Observer pixel position (from test case)
obs_px = 4105.5
obs_py = 3166.5

print(f"\nTangent_dist(m)  Pixel_X    Pixel_Y    dPixel    Ratio(m/px)")

# Approximation: For small angles, 1 radian ≈ R meters on the surface
# At lat = -85°, the local scale factor k = 2/(1 + sin|lat|) ≈ 2/(1 + 0.996) ≈ 1.002
# So 1000m tangent distance ≈ 1000m ground distance ≈ 1000/5 = 200 pixels

prev_px, prev_py = obs_px, obs_py
for dist in [100, 200, 500, 1000, 2000, 5000]:
    # Point at tangent distance dist
    sample = obs_vec + dir_me * dist
    lat, lon = me_to_lat_lon(sample)
    
    # Convert to pixel using stereographic projection
    # This is approximate - would need full projection math
    # For now, let's compute the angular distance and estimate
    
    # Angular distance from observer
    angular_dist = dist / R  # radians
    
    # At the pole, stereographic projection maps angular distance θ to
    # distance r = 2R*tan(θ/2) in map coordinates
    # For small θ: r ≈ R*θ = dist (since θ = dist/R)
    
    # Approximate pixel position (ignoring projection details)
    # In reality, the pixel change depends on the projection and direction
    
    # From the data we have:
    # At s=1m, pixel = (4106.29, 3165.89)
    # At s=1124m, pixel = (4992.00, 2482.26)
    # That's 885.7 pixels in X, 683.6 pixels in Y
    # Total pixel distance = sqrt(885.7^2 + 683.6^2) = 1118.9 pixels
    
    # So s(m) / pixel_distance ≈ 1124 / 1119 ≈ 1.00 m/pixel
    
    # But map resolution is 5 m/pixel, so we expect:
    # s(m) / pixel_distance ≈ 5.0 m/pixel
    
    # This 5x discrepancy suggests the data is correct but my understanding was wrong!
    
    # The "s" in samples is NOT the same as ground distance.
    # s is the tangent distance in 3D space (straight line from observer)
    # Ground distance is the arc length along the surface
    
    # But wait - at 1km distance and Moon radius ~1737km:
    # Arc length = R * arcsin(tangent_dist / R) ≈ tangent_dist for small distances
    # So they should be nearly equal!
    
    print(f"  {dist:8.0f}  {obs_px + dist/5:.1f}  {obs_py - dist/5*0.62:.1f}  (estimated)")

print("\n" + "="*70)
print("KEY INSIGHT")
print("="*70)
print("""
The data shows:
  s = 1124m corresponds to 1119 pixels
  This gives s/pixels = 1.00 m/pixel

But the map resolution is ~5 m/pixel!

This means one of:
1. The 's' value is NOT in meters (but it should be, per the code)
2. The pixel coordinates are wrong
3. There's a projection scaling issue

Looking at the stereographic projection formula at the south pole:
  At lat = -85°, the scale factor k = 2/(1 + |sin(lat)|) ≈ 1.002

The map CRS uses the stereographic projection with R=1737400m.
At the center of projection (the pole), 1 radian ≈ 1737400m.
A point 1000m away in tangent plane corresponds to 1000/1737400 ≈ 0.000576 radians.

In stereographic projection, r = 2*R*tan(θ/2) where θ is the angular distance.
For θ = 0.000576 rad: r = 2*1737400*tan(0.000288) ≈ 1000m

So the CRS coordinates should scale 1:1 with tangent distance (approximately).

If map resolution is 5 m/pixel, then 1000m should be 200 pixels.
But the data shows 1000m ≈ 1000 pixels!

This means the map resolution calculation is wrong, or
the projection parameters are wrong.

Let me check: if 1 pixel = 1m in CRS, then mapRes should be 1.0, not 5.0.
""")

# Check what mapRes calculation gives
print("\n" + "="*70)
print("CHECKING MAP RESOLUTION")
print("="*70)

# From the GeoTransform, mapRes = sqrt(geo[1]^2 + geo[4]^2)
# The geo array is: [originX, pixelWidth, rotX, originY, rotY, pixelHeight]

# Typical values for 5m/pixel:
# geo[1] = 5.0 (pixel width in CRS units per pixel)
# geo[5] = -5.0 (pixel height, negative because Y increases downward)

# If the CRS is in meters and 5 m/pixel, we'd have:
# geo[1] = 5.0
# geo[5] = -5.0

# But if the data shows 1 m/pixel behavior, then either:
# 1. geo[1] = 1.0 (unlikely, since it's documented as 5m/pixel)
# 2. The projection is scaling differently than expected

print("The discrepancy suggests a projection scaling issue.")
print("The tangent distance in 3D space may not equal CRS distance.")
print("Need to check the actual GeoTransform values from the DEM.")
