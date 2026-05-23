Help me debug this program.
For context, read docs/DESCRIPTION.md

I'm assuming the reference algorithm
(@horizongen/ReferenceHorizonGenerator.cs) is correct, although there
are questions about how it stepping along rays may miss actual
elevation angle maxima.

I'm trying to debug @horizongen/QuadTreeHorizonGenerator.cs.  There
are numerous observer locations and azimuths where the reference
horizon elevation differs substantially from the quad tree horizon
elevation.  For now, I'm trying to reach consistent agreement within
0.1 deg.

Because the quad tree algorithm is GPU-based, debugging it is
difficult.  As part of my debugging strategy, we've implemented
CPU-based emulations of ray-casting for both the reference and the
quad tree algorithms.  These are @horizongen/ReferenceRayEmulator.cs
and @horizongen/QuadTreeRayEmulator.cs.  I am not certain these
classes accurately emulate the actual algorithms.

We have 