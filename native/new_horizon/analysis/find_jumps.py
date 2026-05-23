import csv
import sys

# Read the QT DEM0 trace
with open('output_debug/diag_qt_dem0.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

# Find where distance jumps backward
print('Looking for distance jumps (checking all rows):')
prev_dist = 0
jump_found = []
for i, r in enumerate(rows):
    dist = float(r['dist_m'])
    if dist < prev_dist - 10:  # Jump backward by more than 10m
        jump_found.append((i, prev_dist, dist))
    prev_dist = dist

print(f'Found {len(jump_found)} backward jumps')
for idx, prev, curr in jump_found[:5]:
    print(f'  Index {idx}: {prev:.2f}m -> {curr:.2f}m (delta={curr-prev:.2f}m)')

# Show around first jump
if jump_found:
    idx = jump_found[0][0]
    print(f'\nAround jump at index {idx}:')
    print('idx, dist_m, pixel_x, pixel_y, slope')
    for i in range(max(0,idx-3), min(idx+5, len(rows))):
        r = rows[i]
        dist = float(r['dist_m'])
        px = float(r['pixel_x'])
        py = float(r['pixel_y'])
        slope = float(r['slope'])
        print(f'{i}, {dist:.2f}, {px:.2f}, {py:.2f}, {slope:.2f}')
