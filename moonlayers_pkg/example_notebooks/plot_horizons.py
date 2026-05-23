import numpy as np
import matplotlib.pyplot as plt
import 

dem_filename = "your_file.bin"  # replace with your file name

def get_old_horizon(filename, patchx, patchy):
    offset = ((patchy * 128) + patchx) * 1440 * 4 + 7 * 4
    return get_horizon(filename, offset)

def get_new_horizon(filename, patchx, patchy):
    offset = ((patchy * 128) + patchx) * 1440 * 4
    return get_horizon(filename, offset)

def get_horizon(filename, offset):
    with open(filename, "rb") as f:
        f.seek(offset)
        data = np.frombuffer(f.read(1440 * 4), dtype=np.float32)
    return data

pixelx = 0  # replace with desired pixel x-coordinate
pixely = 0  # replace with desired pixel y-coordinate

horizon1 = get_new_horizon(filename, pixelx, pixely)  # replace with desired pixel coordinates
horizon2 = get_old_horizon(filename, pixelx, pixely)  # replace with desired pixel coordinates

# Plot both arrays in a single plot
plt.plot(horizon1, label='horizon1')
plt.plot(horizon2, label='horizon2')
plt.legend()
plt.xlabel('Index')
plt.ylabel('Value')
plt.title('Horizon Arrays')
plt.show()
