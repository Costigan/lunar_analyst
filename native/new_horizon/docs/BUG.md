# Tangent vs. Chord Distance Mismatch in QuadTreeHorizonGenerator

## Problem Description
The `QuadTreeHorizonGenerator` produces horizon elevations that are systematically lower (more negative slope) than the `ReferenceHorizonGenerator`, with the error increasing significantly with distance. At ranges of ~400km, the discrepancy exceeds the 0.1-degree error budget.

### Root Cause
There is a **geometric definition mismatch** between the CPU-side ray generation and the GPU-side slope calculation regarding the definition of "distance" (`s`).

1.  **CPU Construction (`BuildRaySamples`):**
The algorithm marches along a linear ray in the local tangent plane. The distance parameter `s` accumulated and used for polynomial fitting corresponds to the **Tangent Distance**:
$$s_{tangent} = R \tan(\theta)$$
where $\theta$ is the central angle.

2.  **GPU Kernel Calculation (`QuadTreeRayCastKernel`):**
The kernel uses the interpolated distance `trueDist` (derived from `s`) as the **Chord Distance** in the spherical drop calculation. The formula used is:
$$z_{local} = \frac{(h - z_{obs})(2R + h + z_{obs}) - s^2}{2(R + z_{obs})}$$
This formula effectively solves for the vertical drop $z$ given the hypotenuse $s$. For this to be geometrically correct, $s$ **must be the 3D Chord Length**:
$$s_{chord} = 2R \sin(\theta/2)$$

3.  **The Mismatch:**
Since $\tan(\theta) > 2\sin(\theta/2)$ for all $\theta > 0$, the Tangent Distance is always larger than the Chord Distance.
    *   **Effect:** The GPU kernel receives an inflated distance $s$.
    *   **Result:** The calculated vertical drop $z$ is exaggerated (too deep). When this overly deep $z$ is divided by the horizontal distance $x$ to get slope ($z/x$), the resulting slope is **more negative** than it should be.

## Reproduction
A reproduction test case has been added in `tests/HorizonGen.Tests/DebugDistanceMismatch.cs`.
Run it with:
```bash
dotnet test tests/HorizonGen.Tests/HorizonGen.Tests.csproj --filter "FullyQualifiedName=moonlib.tests.DebugDistanceMismatch.DemonstrateSlopeDivergenceAtDistance"
```

## Solution Strategy
The fix should be applied on the **CPU side** to ensure the inputs to the GPU are consistent with the kernel's geometric assumptions. We should **not** modify the GPU kernel, as its math is correct for Chord Distance.

### Implementation Plan
1.  **Modify `BuildRaySamples` (or the call site in `CalculateRaySegments`):**
    Instead of accumulating the tangent step size for the `s` parameter, we must calculate the true **3D Chord Distance** from the observer to each sample point.

    ```csharp
    // Current (Approximate Logic):
    s += step; // Linear accumulation of tangent steps

    // Proposed Fix:
    Vector3d p_obs = ...; // Observer 3D position
    Vector3d p_sample = ...; // Sample 3D position
    s = (p_sample - p_obs).Length; // Exact 3D Chord Length
    ```

2.  **Update Polynomial Fitting:**
    Use this new `s_{chord}` as the independent variable for fitting the polynomials $x(s)$ and $y(s)$, and for the planar-to-chord mapping.

3.  **Verify:**
    Re-run `DebugDistanceMismatch` to confirm the slopes now align with the Reference implementation.

## More discussion

Question: Is the Chord distance larger than the tangent distance?
That is, at 1000 km, the chord distance is 1029 km?

Answer: No, it's the other way around. The Tangent Distance is larger than the Chord Distance.

 * Tangent Distance ($s_{tangent}$): The distance along the flat plane touching the observer's position. This line
   goes "off into space" away from the curved surface.
 * Chord Distance ($s_{chord}$): The straight-line distance through the interior of the moon connecting the observer
   to the target point.

As you go further out:
1. The Chord is the shortest path through the sphere.
2. The Tangent line separates further and further from the surface.

At 1000 km (Tangent Distance):
If you walk 1000 km along the flat tangent plane:
* $\theta = \arctan(1000 / 1737.4) \approx 29.9^\circ$
* The corresponding Chord Distance to the point on the surface "below" that tangent point is $2 \cdot 1737.4 \cdot
  \sin(29.9^\circ / 2) \approx 896 \text{ km}$.

So, if the algorithm thinks it's at s = 1000 km (Tangent), but the actual physical Chord distance to that point is
only 896 km, the Kernel is plugging 1000 into a formula that expects 896.

* Kernel thinks: "I am 1000km away." -> Calculates massive drop for 1000km.
* Reality: You are looking at a point that is geometrically only 896km away (straight line).
* Result: The kernel calculates a drop suitable for a point much further away, pushing the terrain way down. This
  makes the slope more negative (lower horizon).

Correction:
The 29km difference I cited before was a rough approximation, but the direction was wrong in my previous explanation
(I implied Tangent > Chord, which is correct, but the magnitude logic was flipped in the example).
 * Tangent = 1000 km
 * Chord $\approx$ 956 km (actually closer to 956km if we map arc length, but let's stick to the geometry).

Bottom Line: Tangent Distance is always larger. Using it as the Chord Distance makes the kernel calculate too much
curvature drop, resulting in artificially low horizons.