from osgeo import osr, gdal
import math

# Define Projection
wkt = """PROJCS["unnamed",GEOGCS["unknown",DATUM["unknown",SPHEROID["unnamed",1737400,0]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]],PROJECTION["Stereographic"],PARAMETER["latitude_of_origin",-85.42088],PARAMETER["central_meridian",31.6218],PARAMETER["scale_factor",1],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH]]"""

srs = osr.SpatialReference()
srs.ImportFromWkt(wkt)

# GeoTransform
gt = [-2680.5, 1, 0, 2999.5, 0, -1]

def pixel_to_geo(px, py):
    x = gt[0] + px * gt[1] + py * gt[2]
    y = gt[3] + px * gt[4] + py * gt[5]
    return x, y

def get_convergence(px, py):
    # Calculate convergence using PROJ (if available) or manual formula
    # Grid Convergence gamma is the angle between True North and Grid North.
    # For stereographic, gamma = lon - lon0 (roughly, at pole)
    
    # Let's get Lat/Lon
    x, y = pixel_to_geo(px, py)
    
    ct = osr.CoordinateTransformation(srs, srs.CloneGeogCS())
    lon, lat, z = ct.TransformPoint(x, y)
    
    # Manual calculation for Stereographic
    # gamma = (lon - lon0) * sin(lat0) ? No.
    # For Polar Stereographic: gamma = lon - lon0 (if lat0 = 90)
    # Here lat0 = -85.42.
    
    # Better way: sample a point slightly North (True North) and see where it lands in Grid.
    # True North from (lat, lon) is (lat + delta, lon).
    
    lat_delta = 0.01
    lon2, lat2 = lon, lat + lat_delta
    
    ct_inv = osr.CoordinateTransformation(srs.CloneGeogCS(), srs)
    x2, y2, z2 = ct_inv.TransformPoint(lon2, lat2)
    
    dx = x2 - x
    dy = y2 - y
    grid_angle_rad = math.atan2(dx, dy) # Angle of True North relative to Grid Y (North)
    grid_angle_deg = math.degrees(grid_angle_rad)
    
    return grid_angle_deg, lat, lon

print("Calculating Grid Convergence Difference...")

# Patch 31 Center
px1, py1 = 4032, 860
gamma1, lat1, lon1 = get_convergence(px1, py1)
print(f"Patch 31 Center ({px1}, {py1}): Lat={lat1:.4f}, Lon={lon1:.4f}, Convergence={gamma1:.4f} deg")

# Patch 32 Center
px2, py2 = 4160, 860
gamma2, lat2, lon2 = get_convergence(px2, py2)
print(f"Patch 32 Center ({px2}, {py2}): Lat={lat2:.4f}, Lon={lon2:.4f}, Convergence={gamma2:.4f} deg")

diff = gamma2 - gamma1
print(f"Difference: {diff:.4f} deg")

# Calculate ray deviation
# The ray is cast at Azimuth 347.25 relative to True North.
# Grid Azimuth = True Azimuth - Convergence.
# Grid Azimuth 1 = 347.25 - gamma1
# Grid Azimuth 2 = 347.25 - gamma2
# Difference in Grid Azimuth = -(gamma2 - gamma1) = -diff.

print(f"Ray rotation error at boundary: {abs(diff):.4f} degrees")
