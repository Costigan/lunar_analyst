import math
import matplotlib.pyplot as plt

# Lunar reference sphere radius in meters
R = 1737400

# Origin point on equator
origin_cart = (R, 0, 0)

# Distances along the ray
distances = [1, 10, 100, 1000, 10000, 100000, 1000000]

# Function to convert Cartesian to lat/lon
def cart_to_latlon(x, y, z):
    lat = math.degrees(math.asin(z / R))
    lon = math.degrees(math.atan2(y, x))
    return lat, lon

# Function to calculate great circle distance
def great_circle_distance(lat1, lon1, lat2, lon2, radius):
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlon = lon2_rad - lon1_rad
    a = math.sin(lat1_rad) * math.sin(lat2_rad) + math.cos(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon)
    c = math.acos(a)
    return radius * c

# Calculate origin lat/lon
origin_lat, origin_lon = cart_to_latlon(*origin_cart)

geodesic_distances = []
deltas = []

for d in distances:
    # Point along the ray
    point_cart = (R, 0, d)
    
    # Distance from center
    dist_from_center = math.sqrt(R**2 + d**2)
    
    # Foot of perpendicular on sphere
    foot_cart = (R / dist_from_center * point_cart[0], 
                 R / dist_from_center * point_cart[1], 
                 R / dist_from_center * point_cart[2])
    
    # Convert foot to lat/lon
    foot_lat, foot_lon = cart_to_latlon(*foot_cart)
    
    # Calculate geodesic distance
    geo_dist = great_circle_distance(origin_lat, origin_lon, foot_lat, foot_lon, R)
    geodesic_distances.append(geo_dist)
    deltas.append(geo_dist - d)

print(distances)
print(deltas)

# Plot
plt.figure(figsize=(10, 6))
plt.loglog(distances, [abs(delta) for delta in deltas], marker='o')
plt.xlabel('Distance along ray (meters)')
plt.ylabel('Absolute delta geodesic distance (meters)')
plt.title('Absolute Delta vs Distance along Ray')
plt.grid(True)
plt.savefig('abs_delta_vs_ray.png')
plt.show()
