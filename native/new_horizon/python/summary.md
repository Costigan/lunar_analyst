# Summary of polynomial-approximation analysis

**Files changed**
- `examine_polynomial_accuracy.py`
  - Added robust PROJ handling (set `PROJ_LIB` from venv site-packages).
  - Added a pure‑Python oblique stereographic fallback when PROJ/GDAL is unavailable.
  - Switched to a constrained polynomial fit that enforces the ray origin match (s = 0).
  - Added CSV output for worst‑case error tables and aggregated south‑pole origin sampling.
  - Increased south‑pole random origins sampled to 100 (per requested run).

**What I ran**
- Executed the script after the edits; it completed and produced the CSV files:
  - `examine_polynomial_accuracy.csv`
  - `examine_polynomial_accuracy_south_pole_origins_aggregated.csv`

**Fitting form — what "order 3" means**
- Rays are parameterized by a normalized distance parameter `s ∈ [0, 1]` (0 at the origin, 1 at the ray endpoint).
- For each projected coordinate axis we fit a cubic polynomial constrained to pass through the origin point (s = 0):

  x(s) = x0 + a1*s + a2*s^2 + a3*s^3
  y(s) = y0 + b1*s + b2*s^2 + b3*s^3

  - `x0`, `y0` are the projected coordinates at the ray origin and are enforced exactly.
  - The free coefficients `{a1,a2,a3}` and `{b1,b2,b3}` are obtained by least‑squares on the sampled projected points.
  - Coefficients are stored in `numpy.polyval` order for evaluation as `[a3, a2, a1, x0]` and `[b3, b2, b1, y0]`.

**Results observed (representative)**
- The script computes worst‑case maximum errors (meters) for degrees 0..5 at distances: 0 m, 100 m, 1 km, 10 km, 100 km, 1000 km.
- Example worst‑case entries (degree 3) from the run:
  - 0 m: 0.000000 m
  - 100 m: 0.000000 m
  - 1 km: 0.000000 m
  - 10 km: 0.000000 m
  - 100 km: ~0.000425 m
  - 1000 km: ~46.383497 m
- Aggregated worst‑case errors across 100 random origins near the south pole were written to `examine_polynomial_accuracy_south_pole_origins_aggregated.csv` for further inspection.

**Conclusion: Is order 3 acceptable?**
- For most azimuths and ranges up to a few hundred kilometers, the constrained cubic (order 3) fit yields excellent accuracy (typically sub‑meter and often sub‑meter at 100 km in our tests).
- However, the worst‑case at the extreme 1000 km endpoint can reach tens of meters (e.g., ~46 m in one tested worst azimuth). Therefore:
  - Use order 3 as the default when you need a compact representation and target sub‑meter or near‑sub‑meter accuracy out to ~100 km.
  - If you require guaranteed small worst‑case errors at 1000 km for every azimuth, use a higher polynomial order (4 or 5), an adaptive/piecewise fit, or a different basis (e.g., splines or orthogonal polynomials).

**Next steps (optional)**
- Attach or print the generated CSVs here for review.
- Re-run with increased azimuth sampling or tighter step resolution to refine worst‑case bounds.
- Compare degree 3 vs 4 vs 5 worst‑case numbers automatically and produce a small summary table of improvements.
- Re-enable plots and save PNGs for visual inspection.

---
Created by edits and runs performed on `examine_polynomial_accuracy.py` in this workspace.
