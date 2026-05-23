import csv
import math

# Read the QT DEM0 trace
with open('output_debug/diag_qt_dem0.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

# Read samples file for polynomial coefficients inspection
print('Reading samples file...')
with open('output_debug/diag_qt_dem0.samples.txt') as f:
    samples = []
    for line in f:
        parts = line.strip().split(':')
        s, x, y = float(parts[0]), float(parts[1]), float(parts[2])
        samples.append((s, x, y))

print(f'Samples: {len(samples)} points')
print(f'First sample: s={samples[0][0]:.6f}km, x={samples[0][1]:.2f}, y={samples[0][2]:.2f}')
print(f'Last sample: s={samples[-1][0]:.6f}km, x={samples[-1][1]:.2f}, y={samples[-1][2]:.2f}')

x0, y0 = samples[0][1], samples[0][2]
mapRes = 1.0  # 1m/pixel for DEM0

# Compute planar distance at each sample point
print('\nPlanar distance vs s*1000 at sample points:')
print('s(km), s*1000(m), planar(m), ratio')
for s, x, y in samples[:5]:
    dx = (x - x0) * mapRes
    dy = (y - y0) * mapRes
    planar = math.sqrt(dx*dx + dy*dy)
    s_m = s * 1000
    ratio = planar / s_m if s_m > 0 else 0
    print(f'{s:.6f}, {s_m:.2f}, {planar:.2f}, {ratio:.4f}')

# Find where s ~ 0.5 km
print('\nAround s = 0.5km threshold:')
for i, (s, x, y) in enumerate(samples):
    if 0.45 < s < 0.6:
        dx = (x - x0) * mapRes
        dy = (y - y0) * mapRes
        planar = math.sqrt(dx*dx + dy*dy)
        print(f's={s:.6f}km, planar={planar:.2f}m, s*1000={s*1000:.2f}m, diff={planar-s*1000:.2f}m')
