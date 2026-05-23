import numpy as np
import matplotlib.pyplot as plt

R = 1737400
distances = np.linspace(1, 1000, 500)

# Height above surface
h = R * (1 - np.cos(distances / R))

dist_from_center = np.sqrt(R**2 + distances**2)
foot_x = R / dist_from_center * R
foot_y = np.zeros_like(distances)
foot_z = R / dist_from_center * distances

vector_to_foot_x = foot_x - R
vector_to_foot_y = np.zeros_like(distances)
vector_to_foot_z = foot_z

dot_product = vector_to_foot_z  # since ray_direction = (0,0,1), dot is just the z-component
mag_vector = np.sqrt(vector_to_foot_x**2 + vector_to_foot_y**2 + vector_to_foot_z**2)
cos_theta = dot_product / mag_vector
angular_separations = np.degrees(np.arccos(cos_theta))

# Plot
plt.figure(figsize=(10, 6))
plt.semilogx(distances, angular_separations)
plt.xlabel('Distance along ray (meters)')
plt.ylabel('Angular separation (degrees)')
plt.title('Angular Separation vs Distance along Ray')
plt.grid(True)
plt.show()