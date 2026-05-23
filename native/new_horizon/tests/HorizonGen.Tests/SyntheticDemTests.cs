using moonlib.horizon;
using System.Drawing;
using System.Globalization;

namespace moonlib.tests
{
    public static class PrivateApi
    {
        // Minimal shims to access internal methods via reflection or direct if internal made visible.
        public static Pyramid BuildOrLoadPyramid(QuadTreeHorizonGenerator gen, ElevationMap dem)
        {
            var mi = typeof(QuadTreeHorizonGenerator).GetMethod("BuildOrLoadPyramid", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            return (Pyramid)mi!.Invoke(gen, new object[] { dem })!;
        }
        public static PyramidView PyramidViewFrom(Pyramid p)
        {
            return new PyramidView
            {
                DataLevel0 = p.DataLevel0!.View,
                DataMips = p.DataMips!.View,
                Infos = p.Infos!.View,
                Map = p.Map,
                Proj = p.Proj,
                Levels = p.CpuInfos!.Length
            };
        }
        public static (RaySegment[] segs, bool isCompact) CalculateRaySegments(
            QuadTreeHorizonGenerator gen,
            List<Pyramid> pyramids,
            PyramidView primaryPV,
            int tileColBase, int tileRowBase, int tileW, int tileH,
            int numAzimuths, float maxDist, float observerElevation = 0f)
        {
            var mi = typeof(QuadTreeHorizonGenerator).GetMethod("CalculateRaySegments", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            object? result = mi!.Invoke(gen, new object[] { pyramids, primaryPV, tileColBase, tileRowBase, tileW, tileH, numAzimuths, maxDist, observerElevation });
            // CalculateRaySegments now returns (RaySegment[], bool, GridConvergenceInfo) - extract first two elements
            var tuple = (System.Runtime.CompilerServices.ITuple)result!;
            return ((RaySegment[])tuple[0]!, (bool)tuple[1]!);
        }
        public static float EvalCubic(float x0, float a1, float a2, float a3, float a4, float s)
        {
            var mi = typeof(QuadTreeHorizonGenerator).GetMethod("EvalCubic", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
            return (float)mi!.Invoke(null, new object[] { x0, a1, a2, a3, a4, s })!;
        }
    }

    public static class TestUtils
    {
        public static ElevationMap CreateSyntheticDem(int w, int h, Func<int,int,float> fn)
        {
            float[,] elev = new float[h, w];
            for (int r = 0; r < h; r++)
                for (int c = 0; c < w; c++)
                    elev[r, c] = fn(c, r);
            // Provide minimal GeoTransform and projection
            double[] gt = new double[] { 0.0, 1.0, 0.0, 0.0, 0.0, -1.0 };
            var map = new ElevationMap(elev, ElevationMap.LongLatProj, gt);
            return map;
        }
    }

    /// <summary>
    /// Tests using synthetically generated DEMs (e.g., perfectly flat planes, single peaks)
    /// to verify the geometric accuracy of the horizon generation logic.
    /// </summary>
    [TestClass]
    public class SyntheticDemTests
    {
        private static double AzimuthDegreesFromIndex(int index, int samples)
        {
            return index * 360.0 / Math.Max(1, samples);
        }

        private static ElevationMap MakeFlatDem(int width, int height, double pixelDeg = 0.01)
        {
            var elev = new float[height, width]; // all zeros
            // GeoTransform: [originX, pixelSizeX, rotX, originY, rotY, pixelSizeY]
            // LongLat degrees CRS, row increases downward -> pixelSizeY negative
            var gt = new double[] { 0.0, pixelDeg, 0.0, 0.0, 0.0, -pixelDeg };
            var em = new ElevationMap(elev, ElevationMap.LongLatProj, gt);
            return em;
        }

        private static ElevationMap MakePeakDem(int width, int height, Point peak, float peakHeightMeters, double pixelDeg = 0.01)
        {
            var elev = new float[height, width];
            elev[peak.Y, peak.X] = peakHeightMeters;
            var gt = new double[] { 0.0, pixelDeg, 0.0, 0.0, 0.0, -pixelDeg };
            return new ElevationMap(elev, ElevationMap.LongLatProj, gt);
        }

        private static ElevationMap MakeStereoPeakDem(int width, int height, Point peak, float peakHeightMeters, double pixelMeters = 30.0)
        {
            var elev = new float[height, width];
            elev[peak.Y, peak.X] = peakHeightMeters;
            // GeoTransform: [originX, pixelSizeX, rotX, originY, rotY, pixelSizeY]
            // Top-Left origin at (0, height * pixelMeters) so (0,0) is bottom-left in Cartesian
            var gt = new double[] { 0.0, pixelMeters, 0.0, height * pixelMeters, 0.0, -pixelMeters };

            // Proj4 string for Stereographic centered at 0,0
            string stereoProj = "+proj=stere +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs";

            return new ElevationMap(elev, stereoProj, gt);
        }

        /// <summary>
        /// Verifies that on a flat plane, the <see cref="ReferenceHorizonGenerator"/> (CPU implementation)
        /// calculates horizon angles near zero.
        /// </summary>
        //[TestMethod]  // works but is slow
        public void FlatPlane_HorizonAngles_AreNearZero()
        {
            var dem = MakeFlatDem(128, 128);
            var dems = new List<ElevationMap> { dem };
            var refGen = ReferenceHorizonGenerator.Singleton;

            // Place observer somewhere well inside the DEM
            var obs = new Point(64, 64);
            var (latDeg, lonDeg) = dem.Point2LatLonDeg(obs);
            var latlon_origin = new LatLonOrigin(latDeg, lonDeg, 0f);
            var result = refGen.GenerateFromLatLon(latlon_origin, dems);

            // Expect angles near 0 deg everywhere
            double mae = 0.0;
            int n = 0;
            foreach (var deg in result.Elevations)
            {
                if (!float.IsNaN(deg) && !float.IsInfinity(deg))
                {
                    mae += Math.Abs(deg);
                    n++;
                }
            }
            mae /= Math.Max(1, n);
            Assert.IsTrue(mae < 0.2, $"MAE too high on flat plane: {mae}");
        }

        /// <summary>
        /// Verifies that a single peak placed on a flat DEM produces a significant positive horizon angle
        /// at the correct azimuth (East) when viewed from the center.
        /// </summary>
        //[TestMethod]  // works but is slow
        [TestCategory("Fast")]
        public void SinglePeak_ProducesNonZeroAtCorrectAzimuth()
        {
            var width = 128; var height = 128;
            var observer = new Point(64, 64);
            var peak = new Point(96, 64); // due east of observer
            var dem = MakePeakDem(width, height, peak, 500f);
            var dems = new List<ElevationMap> { dem };
            var refGen = ReferenceHorizonGenerator.Singleton;

            (double latDeg, double lonDeg) = dem.Point2LatLonDeg(observer);
            var latlon_origin = new LatLonOrigin(latDeg, lonDeg, 0f);
            var result = refGen.GenerateFromLatLon(latlon_origin, dems);

            // Find max angle azimuth index
            int maxIdx = -1; float maxDeg = float.NegativeInfinity;
            for (int i = 0; i < result.Elevations.Length; i++)
            {
                var deg = result.Elevations[i];
                if (deg > maxDeg)
                {
                    maxDeg = deg; maxIdx = i;
                }
            }

            // Expected azimuth approx 90 degrees (east) in generator indexing
            // Convert index to degrees
            var azDeg = AzimuthDegreesFromIndex(maxIdx, ReferenceHorizonGenerator.HorizonSamples);
            // Accept within 15 degrees (discretization and sampling tolerance)
            Assert.IsTrue(maxDeg > 0.5, $"Peak did not produce significant elevation: {maxDeg}");
            Assert.IsTrue(Math.Abs(azDeg - 90.0) < 1.0, $"Max elevation azimuth not near east (90 deg): {azDeg} deg");
        }

        [TestMethod]
        public void CalculateRaySegments_CompactMode_ProducesCubicSegments()
        {
            int w = 64, h = 64;
            var dem = MakeStereoPeakDem(w, h, new Point(w / 2, h / 2), 0f, 30.0);
            var gen = new QuadTreeHorizonGenerator();
            var pyr = PrivateApi.BuildOrLoadPyramid(gen, dem);
            var pv = PrivateApi.PyramidViewFrom(pyr);

            int tileX = 0, tileY = 0, tileW = 32, tileH = 32;
            int numAz = 64;

            var res = PrivateApi.CalculateRaySegments(gen, new List<Pyramid> { pyr}, pv, tileX, tileY, tileW, tileH, numAz, 200000f);
            var segs = res.segs; var isCompact = res.isCompact;

            int expectedLength = isCompact ? numAz : numAz * tileW * tileH;
            Assert.AreEqual(expectedLength, segs.Length);
            int cubicCount = segs.Count(s => s.SEnd > s.SStart);
            Assert.IsTrue(cubicCount > 0, "No cubic segments were generated");

            pyr.Dispose();
            gen.Dispose();
        }

        [TestMethod]
        public void QuadTreeRayEmulator_ProducesSamplesAtDemBorder()
        {
            var width = 32; var height = 32;
            var observer = new PixelOrigin { X = 0, Y = 0, Z = 0f };
            var peak = new Point(31, 16);
            var dem = MakeStereoPeakDem(width, height, peak, 200f, 30.0);
            string tempDir = Path.Combine(Path.GetTempPath(), "qt_emulator_tests");
            Directory.CreateDirectory(tempDir);
            var csvPath = Path.Combine(tempDir, "edge_emulator.csv");

            var result = QuadTreeRayEmulator.Run(dem, observer, 90.0, csvPath, suppressCsv: false, unifiedStepMode: false);
            Assert.IsTrue(result.Slopes.Length > 0, "Emulator returned no samples");

            var sampleFile = Path.ChangeExtension(csvPath, ".samples.txt");
            Assert.IsTrue(File.Exists(sampleFile), "Sample dump not written");
            var sampleLines = File.ReadAllLines(sampleFile);
            Assert.IsTrue(sampleLines.Length >= 4, "Sample dump did not include minimum points");
            var firstS = double.Parse(sampleLines.First().Split(':')[0], CultureInfo.InvariantCulture);
            var lastS = double.Parse(sampleLines.Last().Split(':')[0], CultureInfo.InvariantCulture);
            // Sample distances are now in kilometers, so convert to meters for comparison
            double spanMeters = (lastS - firstS) * 1000.0;
            Assert.IsTrue(spanMeters >= 100.0 - 1e-3, $"Sample span too short: {spanMeters} meters");
        }

        [TestMethod]
        public void QuadTreeRayEmulator_MatchesReferenceOnSinglePeak()
        {
            var width = 128; var height = 128;
            var observer = new PixelOrigin { X = 64, Y = 64, Z = 0f };
            var peak = new Point(96, 64);
            var dem = MakeStereoPeakDem(width, height, peak, 400f, 30.0);
            string tempDir = Path.Combine(Path.GetTempPath(), "qt_vs_ref_emulator");
            Directory.CreateDirectory(tempDir);

            double azimuth = 90.0;
            var qtResult = QuadTreeRayEmulator.Run(dem, observer, azimuth, Path.Combine(tempDir, "qt.csv"), suppressCsv: true, unifiedStepMode: true);
            var refResult = ReferenceRayEmulator.Run(dem, observer, azimuth, Path.Combine(tempDir, "ref.csv"), suppressCsv: true, unifiedStepMode: true);

            Assert.IsTrue(qtResult.Slopes.Length > 0 && refResult.Slopes.Length > 0, "Emulators produced no samples");
            int compareCount = Math.Min(qtResult.Slopes.Length, refResult.Slopes.Length);
            double maxDiffDeg = 0.0;
            for (int i = 0; i < compareCount; i++)
            {
                double q = qtResult.Slopes[i];
                double r = refResult.Slopes[i];
                if (double.IsNaN(q) || double.IsNaN(r))
                    continue;
                double qDeg = Math.Atan(q) * (180.0 / Math.PI);
                double rDeg = Math.Atan(r) * (180.0 / Math.PI);
                maxDiffDeg = Math.Max(maxDiffDeg, Math.Abs(qDeg - rDeg));
            }

            Assert.IsTrue(maxDiffDeg < 15.0, $"Emulators diverged: max diff {maxDiffDeg} deg");
        }
    }
}
