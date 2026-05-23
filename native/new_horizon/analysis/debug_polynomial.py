import math

# From the samples file: s(km), x(pixels), y(pixels)
samples = [
    (0.001000, 4106.288736, 3165.891218),
    (0.113295, 4194.859783, 3097.528124),
    (0.225590, 4283.430830, 3029.165028),
    (0.337885, 4372.001877, 2960.801929),
    (0.450180, 4460.572922, 2892.438829),
    (0.562475, 4549.143966, 2824.075728),
    (0.674770, 4637.715007, 2755.712626),
    (0.787064, 4726.286045, 2687.349523),
    (0.899359, 4814.857080, 2618.986420),
    (1.011654, 4903.428110, 2550.623318),
    (1.123949, 4991.999137, 2482.260217),
]

# DEM0 parameters
mapRes = 1.0  # 1m/pixel
R = 1737400.0  # Moon radius in meters

x0, y0 = samples[0][1], samples[0][2]

print("Computing planar and chord distances from samples:")
print("s(km), tangent(m), planar(m), chord(m), chord/planar")
print("-" * 60)

# The polynomial is fit from (planar[i], chord[i] - chord[0])
# where chord is 3D distance from observer to terrain point

# For now, approximate chord distance as tangent distance
# (The actual ComputeChordToTerrain is more complex, using terrain)
for i, (s, x, y) in enumerate(samples):
    tangent_m = s * 1000  # s is in km
    dx = (x - x0) * mapRes
    dy = (y - y0) * mapRes
    planar_m = math.sqrt(dx*dx + dy*dy)
    
    # At short range, chord ≈ tangent (spherical effects minimal)
    # The actual function samples terrain and computes 3D distance
    chord_approx = tangent_m  # approximation
    
    ratio = chord_approx / planar_m if planar_m > 0 else 0
    print(f"{s:.6f}, {tangent_m:.2f}, {planar_m:.2f}, {chord_approx:.2f}, {ratio:.4f}")

print("\n" + "=" * 60)
print("KEY INSIGHT:")
print("If chord/planar ≈ 1.0, then C1 should be ≈ 1.0")
print("But C1 = 7.92, which is way off!")
print("")
print("This suggests the UNITS are wrong somewhere in the fitting.")
print("Possibly: samples[i].s is being interpreted wrong,")
print("or mapRes is wrong, or the polynomial is being inverted somehow.")
print("=" * 60)

# Let's check what the polynomial actually computes
print("\nPolynomial coefficients from output:")
C1 = 7.924703598
C2 = -2.504987828E-002
C3 = 1.983546281E-005

print(f"C1={C1}, C2={C2}, C3={C3}")
print("\nEvaluating polynomial at various planar distances:")
print("planar(m), EvalPlanarChord(m), expected_chord(m)")
for planar in [0, 100, 200, 300, 400, 500, 886]:
    result = C1 * planar + C2 * planar**2 + C3 * planar**3
    expected = planar  # At short range chord ≈ planar
    print(f"{planar}, {result:.2f}, {expected:.2f}")
