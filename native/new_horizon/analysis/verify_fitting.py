import numpy as np

# Actual data from verbose output
# mapRes=1.0, so planar = pixel_distance * 1.0
# Data: (planar_m, chordDelta_m)
data = [
    (0.0, 0.0),            # Sample 0
    (111.89, 112.55),      # Sample 1
    (223.77, 224.60),      # Sample 2
    (335.66, 336.87),      # Sample 3
    (447.54, 449.28),      # Sample 4
    (559.43, 561.63),      # Sample 5
    # Continue with estimated values based on pattern
]

# The actual samples go up to planar=1118.85m
# Let's estimate remaining samples
for i in range(6, 11):
    planar = 111.89 * i
    chord = planar * 1.006  # ratio is ~1.006
    data.append((planar, chord))

planar = np.array([d[0] for d in data])
chord = np.array([d[1] for d in data])

print("Input data:")
print("planar(m), chord(m), ratio")
for p, c in data:
    ratio = c / p if p > 0 else 0
    print(f"{p:.2f}, {c:.2f}, {ratio:.6f}")

# The C# code fits: y = c1*x + c2*x^2 + c3*x^3 (no intercept)
# Using least squares: minimize sum((c1*x + c2*x^2 + c3*x^3 - y)^2)
# 
# Design matrix A = [x, x^2, x^3]
# Normal equations: A^T A c = A^T y

x = planar
y = chord

# Build design matrix
A = np.column_stack([x, x**2, x**3])

# Normal equations
ATA = A.T @ A
ATy = A.T @ y

print("\nNormal matrix (ATA):")
print(ATA)
print("\nRHS (ATy):")
print(ATy)

# Solve
try:
    sol = np.linalg.solve(ATA, ATy)
    print(f"\nSolution: c1={sol[0]:.9e}, c2={sol[1]:.9e}, c3={sol[2]:.9e}")
except np.linalg.LinAlgError as e:
    print(f"Failed to solve: {e}")

# Let's also verify with numpy's lstsq
coeffs, residuals, rank, s = np.linalg.lstsq(A, y, rcond=None)
print(f"\nNumPy lstsq: c1={coeffs[0]:.9e}, c2={coeffs[1]:.9e}, c3={coeffs[2]:.9e}")

# What we expect: since chord ≈ planar * 1.006, c1 should be ≈ 1.006
print(f"\nExpected c1 ≈ 1.006 (average ratio)")
print(f"Got c1 = {coeffs[0]:.6f}")

# Evaluate the polynomial
print("\nEvaluating fitted polynomial:")
print("planar(m), actual_chord(m), predicted(m), error(m)")
for p, c in data[:6]:
    pred = coeffs[0]*p + coeffs[1]*p**2 + coeffs[2]*p**3
    print(f"{p:.2f}, {c:.2f}, {pred:.2f}, {pred-c:.2f}")
