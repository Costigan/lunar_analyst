using ILGPU;
using ILGPU.Algorithms;
using ILGPU.Runtime;
using moonlib.math;
using moonlib.spice;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;

namespace moonlib.horizon
{
    /// <summary>
    /// Carries the six components of a GDAL-style GeoTransform to the GPU kernel.
    /// These match GeoTransform[0..5] from ElevationMap.
    /// </summary>
    public struct GeoTransformD
    {
        public double T0; // origin X  (GeoTransform[0])
        public double T1; // pixel width  (GeoTransform[1])
        public double T2; // rotation X  (GeoTransform[2])
        public double T3; // origin Y  (GeoTransform[3])
        public double T4; // rotation Y  (GeoTransform[4])
        public double T5; // pixel height (GeoTransform[5])
    }

    /// <summary>
    /// Result of processing one 128×128 patch.
    /// </summary>
    public readonly struct PatchElevationResult
    {
        /// <summary>Zero-based column index of the patch within the DEM grid.</summary>
        public int PatchCol { get; init; }

        /// <summary>Zero-based row index of the patch within the DEM grid.</summary>
        public int PatchRow { get; init; }

        /// <summary>
        /// Elevation (degrees) for every pixel and every time step.
        /// Dimensions: [128, 128, timeCount].
        /// </summary>
        public float[,,] Data { get; init; }
    }

    /// <summary>
    /// GPU-accelerated computation of solar elevation angles (and eventually
    /// Earth visibility) across patches of a lunar DEM.
    /// </summary>
    public class Lightmaps : IDisposable
    {
        private const int PatchSize = 128;
        private const int DefaultMaxConcurrentStreams = 4;
        private const float RadiansToDegrees = 57.29577951308232f;

        private Context? _context;
        private Accelerator? _accelerator;
        private ConcurrentStack<AcceleratorStream>? _streamPool;
        private MemoryBuffer1D<float, Stride1D.Dense>[]? _patchDemBuffers;
        private readonly int _maxConcurrentStreams;
        private bool _disposed;

        // -----------------------------------------------------------------
        // Pre-compiled kernel delegate (stream-based).
        //
        // demElevation is a per‑patch 128×128 sub‑rectangle of the full DEM.
        // tileColBase / tileRowBase give the absolute pixel origin of that
        // sub‑rectangle so the kernel can compute CRS coordinates.
        // -----------------------------------------------------------------
        private Action<
            AcceleratorStream,
            Index2D,
            ArrayView<float>,                     // demElevation  (128*128 patch)
            int,                                  // demWidth      (always 128)
            int,                                  // demHeight     (always 128)
            GeoTransformD,                        // geotransform
            ProjectionParamsDouble,               // projection parameters
            ArrayView<float>,                     // sunVectors    (flat, 3*timeCount)
            int,                                  // timeCount
            int,                                  // tileColBase  (absolute sample)
            int,                                  // tileRowBase  (absolute line)
            ArrayView<float>                      // output        (128*128*timeCount)
        >? _elevationKernel;

        // -----------------------------------------------------------------
        // Kernel: compute solar elevation angles for one 128×128 patch.
        //
        // Launched with Index2D(PatchSize, PatchSize) — one thread per
        // pixel.  Each thread:
        //   1. Computes the GetMoonMEToENU matrix in double precision.
        //   2. Converts the matrix components to single precision.
        //   3. For every time step, transforms the Moon‑ME sun vector into
        //      local ENU and writes the elevation angle (degrees) into the
        //      output buffer.
        //
        // Output layout: output[ pixelIdx * timeCount + timeIdx ]
        //   where pixelIdx = lineInPatch * 128 + sampleInPatch.
        // -----------------------------------------------------------------
        private static void ComputeElevationAnglesKernel(
            Index2D index,
            ArrayView<float> demElevation,
            int demWidth,
            int demHeight,
            GeoTransformD geotransform,
            ProjectionParamsDouble proj,
            ArrayView<float> sunVectors,
            int timeCount,
            int tileColBase,
            int tileRowBase,
            ArrayView<float> output)
        {
            int sampleInPatch = index.X;
            int lineInPatch = index.Y;

            if (sampleInPatch >= PatchSize || lineInPatch >= PatchSize)
                return;

            int absSample = tileColBase + sampleInPatch;
            int absLine = tileRowBase + lineInPatch;

            // ---- Step 1 — PixelToCRS (double) ----------------------------
            double crsX = geotransform.T0
                        + geotransform.T1 * (double)absSample
                        + geotransform.T2 * (double)absLine;
            double crsY = geotransform.T3
                        + geotransform.T4 * (double)absSample
                        + geotransform.T5 * (double)absLine;

            // ---- Step 2 — Stereographic → lon/lat (radians) (double) -----
            double xp = crsX - proj.FalseEasting;
            double yp = crsY - proj.FalseNorthing;
            double rho = Math.Sqrt(xp * xp + yp * yp);

            double lonRad, latRad;
            if (rho <= 1e-12)
            {
                lonRad = proj.Lon0;
                latRad = proj.Lat0;
            }
            else
            {
                double c = 2.0 * Math.Atan2(rho, 2.0 * proj.K0 * proj.R);
                double sinC = Math.Sin(c);
                double cosC = Math.Cos(c);
                double cosLat0 = Math.Cos(proj.Lat0);
                double sinLat0 = Math.Sin(proj.Lat0);

                latRad = Math.Asin(
                    cosC * sinLat0 + (yp * sinC * cosLat0) / rho);
                lonRad = proj.Lon0 + Math.Atan2(
                    xp * sinC,
                    rho * cosLat0 * cosC - yp * sinLat0 * sinC);
            }

            // ---- Step 3 — Elevation + Moon‑ME Cartesian (double) ---------
            // Read from the 128×128 per‑patch sub‑buffer using local coords.
            double elev = (double)demElevation[lineInPatch * demWidth + sampleInPatch];
            double r = proj.R + elev;

            double cosLat = Math.Cos(latRad);
            double sinLat = Math.Sin(latRad);
            double cosLon = Math.Cos(lonRad);
            double sinLon = Math.Sin(lonRad);

            double moonMeX = r * cosLat * cosLon;
            double moonMeY = r * cosLat * sinLon;
            double moonMeZ = r * sinLat;

            // ---- Step 4 — GetMoonMEToENU rotation basis (double) ---------
            double upX = cosLat * cosLon;
            double upY = cosLat * sinLon;
            double upZ = sinLat;
            double eastX = -sinLon;
            double eastY = cosLon;
            double eastZ = 0.0;
            double northX = -sinLat * cosLon;
            double northY = -sinLat * sinLon;
            double northZ = cosLat;

            double transX = -(moonMeX * eastX + moonMeY * eastY + moonMeZ * eastZ);
            double transY = -(moonMeX * northX + moonMeY * northY + moonMeZ * northZ);
            double transZ = -(moonMeX * upX + moonMeY * upY + moonMeZ * upZ);

            // ---- Step 5 — double → float conversion ----------------------
            float r00 = (float)eastX;  float r01 = (float)northX;  float r02 = (float)upX;
            float r10 = (float)eastY;  float r11 = (float)northY;  float r12 = (float)upY;
            float r20 = (float)eastZ;  float r21 = (float)northZ;  float r22 = (float)upZ;
            float tX  = (float)transX;
            float tY  = (float)transY;
            float tZ  = (float)transZ;

            // ---- Step 6 — Per‑time‑step: sun vector → elevation ----------
            int pixelIdx = lineInPatch * PatchSize + sampleInPatch;

            for (int t = 0; t < timeCount; t++)
            {
                float svX = sunVectors[t * 3 + 0];
                float svY = sunVectors[t * 3 + 1];
                float svZ = sunVectors[t * 3 + 2];

                float enuX = svX * r00 + svY * r10 + svZ * r20 + tX;
                float enuY = svX * r01 + svY * r11 + svZ * r21 + tY;
                float enuZ = svX * r02 + svY * r12 + svZ * r22 + tZ;

                float horizontal = XMath.Sqrt(enuX * enuX + enuY * enuY);
                float elevationRad = XMath.Atan2(enuZ, horizontal);
                float elevationDeg = elevationRad * RadiansToDegrees;

                output[pixelIdx * timeCount + t] = elevationDeg;
            }
        }

        // =================================================================
        // Public API
        // =================================================================

        public Lightmaps(int maxConcurrentStreams = DefaultMaxConcurrentStreams)
        {
            if (maxConcurrentStreams <= 0)
                throw new ArgumentOutOfRangeException(
                    nameof(maxConcurrentStreams), maxConcurrentStreams,
                    "GPU concurrency must be greater than zero.");
            _maxConcurrentStreams = maxConcurrentStreams;
        }

        /// <summary>
        /// Ensure the GPU accelerator, reusable patch-DEM buffers, stream
        /// pool, and pre-compiled kernel are initialised.  Idempotent.
        /// </summary>
        private void EnsureInitialized()
        {
            if (_accelerator is not null)
                return;

            _context = Context.Create(builder =>
                builder.Default()
                       .DebugSymbols(DebugSymbolsMode.Kernel)
                       .EnableAlgorithms());

            var cudaDevice = _context.Devices.FirstOrDefault(
                d => d.AcceleratorType == AcceleratorType.Cuda);
            var oclNvidiaDevice = _context.Devices.FirstOrDefault(
                d => d.AcceleratorType == AcceleratorType.OpenCL
                  && d.Name.IndexOf("NVIDIA", StringComparison.OrdinalIgnoreCase) >= 0);
            var oclAnyDevice = _context.Devices.FirstOrDefault(
                d => d.AcceleratorType == AcceleratorType.OpenCL);
            var chosenDevice = cudaDevice ?? oclNvidiaDevice ?? oclAnyDevice
                               ?? _context.GetPreferredDevice(preferCPU: true);

            Console.WriteLine(
                $"[Lightmaps] Using device: {chosenDevice.Name} ({chosenDevice.AcceleratorType})");

            _accelerator = chosenDevice.CreateAccelerator(_context);

            // Pre-compile the kernel once.
            _elevationKernel = _accelerator.LoadAutoGroupedKernel<
                Index2D,
                ArrayView<float>,
                int,
                int,
                GeoTransformD,
                ProjectionParamsDouble,
                ArrayView<float>,
                int,
                int,
                int,
                ArrayView<float>>(ComputeElevationAnglesKernel);

            // Create stream pool.
            _streamPool = new ConcurrentStack<AcceleratorStream>();
            for (int i = 0; i < _maxConcurrentStreams; i++)
                _streamPool.Push(_accelerator.CreateStream());

            // Pre-allocate reusable per-patch DEM buffers (128×128 floats each).
            // One per concurrent stream so no task ever waits for a buffer.
            int patchPixels = PatchSize * PatchSize;
            _patchDemBuffers = new MemoryBuffer1D<float, Stride1D.Dense>[_maxConcurrentStreams];
            for (int i = 0; i < _maxConcurrentStreams; i++)
                _patchDemBuffers[i] = _accelerator.Allocate1D<float>(patchPixels);
        }

        /// <summary>
        /// Compute per‑pixel solar elevation angles for every 128×128 patch
        /// of the DEM.  Results are yielded as each batch of GPU work
        /// completes so the caller can start consuming them without waiting
        /// for every patch to finish.
        /// </summary>
        /// <param name="demPath">Path to a GeoTIFF DEM readable by ElevationMap.</param>
        /// <param name="times">UTC DateTimes for which sun positions are computed.</param>
        /// <returns>One PatchElevationResult per 128×128 patch, streamed batch‑by‑batch.</returns>
        public IEnumerable<PatchElevationResult> GenerateElevationArray(
            string demPath,
            List<DateTime> times,
            IProgress<float>? progress = null,
            Func<bool>? isCancellationRequested = null)
        {
            EnsureInitialized();

            // ---- 1. Load DEM -------------------------------------------------
            var dem = new ElevationMap(demPath, loadRaster: true);
            if (dem.Elevation is null)
                throw new InvalidOperationException("DEM raster data is null.");

            int demWidth = dem.Width;
            int demHeight = dem.Height;

            if (demWidth % PatchSize != 0 || demHeight % PatchSize != 0)
                throw new ArgumentException(
                    $"DEM dimensions ({demWidth}×{demHeight}) must be multiples of {PatchSize}.");

            // ---- 2. Generate sun vectors (Moon‑ME, metres) --------------------
            int timeCount = times.Count;
            var sunVecsD = new Vector3d[timeCount];
            var earthVecsD = new Vector3d[timeCount];
            for (int t = 0; t < timeCount; t++)
            {
                sunVecsD[t] = SpiceManager.SunPosition_meters(times[t]);
                earthVecsD[t] = SpiceManager.EarthPosition_meters(times[t]);
            }

            // ---- 3. Convert to single precision (for GPU upload) ----------------
            float[] sunVecsFlat = new float[timeCount * 3];
            float[] earthVecsFlat = new float[timeCount * 3];
            for (int t = 0; t < timeCount; t++)
            {
                int off = t * 3;
                sunVecsFlat[off + 0] = (float)sunVecsD[t].X;
                sunVecsFlat[off + 1] = (float)sunVecsD[t].Y;
                sunVecsFlat[off + 2] = (float)sunVecsD[t].Z;
                earthVecsFlat[off + 0] = (float)earthVecsD[t].X;
                earthVecsFlat[off + 1] = (float)earthVecsD[t].Y;
                earthVecsFlat[off + 2] = (float)earthVecsD[t].Z;
            }

            // ---- 4. Build projection / geotransform params ---------------------
            var projD = new ProjectionParamsDouble
            {
                R = dem.SrsDescriptor.R,
                Lat0 = dem.SrsDescriptor.lat0,
                Lon0 = dem.SrsDescriptor.lon0,
                K0 = dem.SrsDescriptor.k0,
                FalseEasting = dem.SrsDescriptor.FalseEasting,
                FalseNorthing = dem.SrsDescriptor.FalseNorthing,
            };

            var gt = new GeoTransformD
            {
                T0 = dem.GeoTransform[0],
                T1 = dem.GeoTransform[1],
                T2 = dem.GeoTransform[2],
                T3 = dem.GeoTransform[3],
                T4 = dem.GeoTransform[4],
                T5 = dem.GeoTransform[5],
            };

            // ---- 5. Upload sun / earth vectors to GPU once (small) -------------
            var gpuSunVectors = _accelerator!.Allocate1D<float>(sunVecsFlat.Length);
            var gpuEarthVectors = _accelerator.Allocate1D<float>(earthVecsFlat.Length);

            try
            {
                gpuSunVectors.CopyFromCPU(sunVecsFlat);
                gpuEarthVectors.CopyFromCPU(earthVecsFlat);

                // ---- 6. Process patches in batched streams ---------------------
                int patchesX = demWidth / PatchSize;
                int patchesY = demHeight / PatchSize;
                int totalPatches = patchesX * patchesY;
                int perPatchOutputSize = PatchSize * PatchSize * timeCount;
                int patchPixels = PatchSize * PatchSize;
                int patchesProcessed = 0;

                for (int batchStart = 0;
                     batchStart < totalPatches;
                     batchStart += _maxConcurrentStreams)
                {
                    int batchSize = Math.Min(_maxConcurrentStreams,
                                             totalPatches - batchStart);
                    var batchTasks = new Task<PatchElevationResult>[batchSize];

                    for (int i = 0; i < batchSize; i++)
                    {
                        int patchIdx = batchStart + i;
                        int patchRow = patchIdx / patchesX;
                        int patchCol = patchIdx % patchesX;

                        int tileColBase = patchCol * PatchSize;
                        int tileRowBase = patchRow * PatchSize;

                        // Extract this patch's 128×128 DEM sub‑rectangle on CPU.
                        float[] patchDem = new float[patchPixels];
                        var elev = dem.Elevation;
                        for (int y = 0; y < PatchSize; y++)
                        {
                            int srcRow = tileRowBase + y;
                            int dstOff = y * PatchSize;
                            for (int x = 0; x < PatchSize; x++)
                                patchDem[dstOff + x] = elev[srcRow, tileColBase + x];
                        }

                        // Snapshot the buffer index for the closure.
                        int bufferIdx = i;
                        var gpuPatchDem = _patchDemBuffers![bufferIdx];

                        batchTasks[i] = Task.Run(() =>
                        {
                            AcceleratorStream stream;
                            while (!_streamPool!.TryPop(out stream))
                                Task.Delay(1).Wait();

                            try
                            {
                                // Upload this patch's DEM data into the reusable buffer.
                                gpuPatchDem.CopyFromCPU(patchDem);

                                using var gpuOutput =
                                    _accelerator.Allocate1D<float>(perPatchOutputSize);

                                _elevationKernel!(
                                    stream,
                                    new Index2D(PatchSize, PatchSize),
                                    gpuPatchDem.View,
                                    PatchSize,
                                    PatchSize,
                                    gt,
                                    projD,
                                    gpuSunVectors.View,
                                    timeCount,
                                    tileColBase,
                                    tileRowBase,
                                    gpuOutput.View);

                                stream.Synchronize();

                                float[] flat = new float[perPatchOutputSize];
                                gpuOutput.CopyToCPU(flat);

                                var data = new float[PatchSize, PatchSize,
                                                     timeCount];
                                for (int y = 0; y < PatchSize; y++)
                                {
                                    for (int x = 0; x < PatchSize; x++)
                                    {
                                        int bufOff = (y * PatchSize + x)
                                                     * timeCount;
                                        for (int t = 0; t < timeCount; t++)
                                            data[y, x, t] = flat[bufOff + t];
                                    }
                                }

                                return new PatchElevationResult
                                {
                                    PatchCol = patchCol,
                                    PatchRow = patchRow,
                                    Data = data,
                                };
                            }
                            finally
                            {
                                _streamPool!.Push(stream);
                            }
                        });

                        ThrowIfCancelled(isCancellationRequested);
                    }

                    Task.WaitAll(batchTasks);

                    for (int i = 0; i < batchSize; i++)
                    {
                        yield return batchTasks[i].Result;
                        patchesProcessed++;
                    }

                    progress?.Report((float)patchesProcessed / totalPatches);
                }
            }
            finally
            {
                gpuEarthVectors.Dispose();
                gpuSunVectors.Dispose();
            }
        }

        // =================================================================
        // Helpers
        // =================================================================

        private static void ThrowIfCancelled(Func<bool>? isCancellationRequested)
        {
            if (isCancellationRequested is not null && isCancellationRequested())
                throw new OperationCanceledException("Lightmaps operation was cancelled.");
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            if (_patchDemBuffers is not null)
            {
                for (int i = 0; i < _patchDemBuffers.Length; i++)
                    _patchDemBuffers[i].Dispose();
                _patchDemBuffers = null;
            }

            if (_streamPool is not null)
            {
                while (_streamPool.TryPop(out var stream))
                    stream.Dispose();
                _streamPool = null;
            }

            _accelerator?.Dispose();
            _accelerator = null;

            _context?.Dispose();
            _context = null;
        }
    }
}
