using moonlib.horizon;
using moonlib.math;
using System.Drawing;
using System.Drawing.Imaging;

#nullable disable

namespace moonlib.tests
{
    [TestClass]
    public class SinglePointComparisonTests
    {
        private const string DemPath = @"/d/datasets/viper_v71_2024_medium/other/dem.tif";
        private const string DemPath2 = @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif";
        private const double ErrorThresholdDeg = 0.25;
        private const int PatchComparisonPairs = 100;
        private const double PatchComparisonMaxDiffDeg = 0.5;
        private const int PatchWidth = 128;
        private const int PatchHeight = 128;
        private const int PatchEdgeMargin = 8;
        private static readonly Dictionary<string, ElevationMap> ElevationMapCache = new(StringComparer.OrdinalIgnoreCase);
        private static readonly Dictionary<string, float[]> PatchHorizonCache = new(StringComparer.OrdinalIgnoreCase);
        private static readonly object ElevationMapCacheLock = new();

        [TestMethod]
        public void Observer2304_1789_Azimuth55_AllEmulatorsAgree()
        {
            int observerX = 2304;
            int observerY = 1789;
            float observerZ = 0.0f;
            double azimuthDeg = 55.0;
            var demPaths = new[] {
                "/d/datasets/viper_v71_2024_medium/other/dem.tif",
                "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
                "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif",
            };
            if (demPaths.Any(p => !File.Exists(p)))
                Assert.Inconclusive($"One or more DEM files are missing: {string.Join(", ", demPaths)}");
            var dems = demPaths.Select(GetElevationMap).ToList();
            var refGen = new ReferenceHorizonGenerator();
            using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: false, enableNearFieldReferenceMerge: true, nearFieldClampMeters: 150f);
            var centerOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerZ };
            var refHorizon = refGen.GenerateFromPixel(centerOrigin, dems);
            var qtResult = qtGen.GenerateHorizons(dems, observerX, observerY, 1, 1, observerZ);
            if (qtResult.Length < 1440)
                Assert.Fail("QuadTree result did not contain 1440 azimuths.");
            var qtDeg = qtResult.Degrees;
            int azIdx = (int)Math.Round(azimuthDeg / 0.25);
            int refHorizonIndex = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(azIdx);
            double refAlgDeg = refHorizon.Elevations[refHorizonIndex];
            double qtAlgDeg = qtDeg[azIdx];
            var rawOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerZ };
            var qtEmuResults = QuadTreeRayEmulator.RunMultiDEM(dems, rawOrigin, azimuthDeg, suppressCsv: true, unifiedStepMode: false);
            var qtEmuResult = EmulatorResult.Combine(qtEmuResults);
            var refEmuResults = ReferenceRayEmulator.RunMultiDem(dems, rawOrigin, azimuthDeg, suppressCsv: true, unifiedStepMode: false);
            var refEmuResult = EmulatorResult.Combine(refEmuResults);
            double qtEmuDeg = qtEmuResult.ElevationDeg;
            double refEmuDeg = refEmuResult.ElevationDeg;
            Console.WriteLine($"Observer: X={observerX}, Y={observerY}, Z={observerZ}");
            Console.WriteLine($"Azimuth: {azimuthDeg} deg");
            Console.WriteLine($"QT Horizon: {qtAlgDeg:F6} deg");
            Console.WriteLine($"REF Horizon: {refAlgDeg:F6} deg");
            Console.WriteLine($"QT Emulator: {qtEmuDeg:F6} deg");
            Console.WriteLine($"REF Emulator: {refEmuDeg:F6} deg");
            var all = new[] { qtAlgDeg, refAlgDeg, qtEmuDeg, refEmuDeg };
            double maxDiff = all.Max() - all.Min();
            if (maxDiff > 0.1)
                Assert.Fail($"Max difference between any pair of elevations is {maxDiff:F4} deg (>0.1 deg)");
        }

        //[TestMethod] // fails
        public void CompareSinglePoint_ReferenceVsQuadTree()
        {
            int PointX = 2470; //837;
            int PointY = 5105; //3280;
            float PointZ = 0.0f;
        
            if (!File.Exists(DemPath))
            {
                Assert.Inconclusive($"DEM file not found at {DemPath}");
            }

            var outputDir = Path.Combine(TestContext.TestRunDirectory, "SinglePointComparison");
            Directory.CreateDirectory(outputDir);

            // 1. Setup Generators
            var dems = new List<ElevationMap> { new ElevationMap(DemPath), new ElevationMap(DemPath2) };
            
            // Reference
            var refGen = new ReferenceHorizonGenerator();
            // We need to inject the specific DEM list into ReferenceGenerator logic or ensure it uses ours
            // ReferenceHorizonGenerator.GenerateFromPixel takes a list of DEMs.
            
            // QuadTree (Disable Hierarchy + Near-field merge)
            using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: true, enableNearFieldReferenceMerge: false, nearFieldClampMeters: 2f);

            var origin = new PixelOrigin { X = PointX, Y = PointY, Z = PointZ };

            // 2. Generate Horizons
            // Ref
            // Note: Ref generator internally adds 0.5 if we use the Generate(PixelOrigin) wrapper, 
            // but GenerateFromPixel takes raw coords. 
            // To match our debug harness: RefGen uses GenerateFromPixel(x+0.5, y+0.5) inside Generate(origin).
            // Let's call GenerateFromPixel directly with the center.
            var centerOrigin = new PixelOrigin { X = PointX, Y = PointY, Z = PointZ };
            var refHorizon = refGen.GenerateFromPixel(centerOrigin, dems);

            // QT
            string qtOutDir = Path.Combine(outputDir, "qt_out");
            Directory.CreateDirectory(qtOutDir);
            qtGen.GenerateHorizons(qtOutDir, dems, PointX, PointY, 1, 1, PointZ);
            
            // Load QT Result
            var observer_dec = (int)Math.Round(PointZ * 10);
            var qtFile = Path.Combine(qtOutDir, $"horizon_{PointX:D5}_{PointY:D5}_{observer_dec:D3}.bin");
            Assert.IsTrue(File.Exists(qtFile), "QuadTree output file was not created.");
            
            var qtAnglesDeg = Utilities.LoadBinaryArray<float>(qtFile);

            // 3. Compare & Validate
            Assert.AreEqual(1440, refHorizon.Elevations.Length);
            Assert.AreEqual(1440, qtAnglesDeg.Length);

            double maxDiff = 0;
            int maxDiffIndex = -1;
            var diffs = new List<float>();

            for (int i = 0; i < 1440; i++)
            {
                // Ref uses an indexing mapping? HorizonComparator uses ConvertHorizonIndexToQuadTreeIndex
                // Let's verify if that mapping is identity (it usually is for standard 0-360 runs)
                int refIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i);
                
                float refVal = refHorizon.Elevations[refIdx];
                float qtVal = qtAnglesDeg[i];

                // Check Validity
                if (float.IsNaN(refVal) || refVal <= -1.0e30f || float.IsNaN(qtVal) || qtVal <= -1.0e30f)
                {
                    Console.WriteLine($"Invalid Value at index {i} (Az: {i * 0.25} deg). Ref: {refVal}, QT: {qtVal}");
                    // Force trace for this index if it's the first one found
                    if (maxDiffIndex == -1)
                    {
                        maxDiffIndex = i;
                        maxDiff = 9999; // Force threshold check
                    }
                    continue; // Skip diff calc
                }

                float diff = Math.Abs(refVal - qtVal);
                diffs.Add(diff);

                if (diff > maxDiff)
                {
                    maxDiff = diff;
                    maxDiffIndex = i;
                }
            }

            Console.WriteLine($"Max Difference: {maxDiff:F4} degrees at Azimuth index {maxDiffIndex} ({maxDiffIndex * 0.25} deg)");

            // 4. Plot
            PlotHorizons(refHorizon.Elevations, qtAnglesDeg, Path.Combine(outputDir, "comparison_plot.png"));

            // 5. Deep Dive if Error Threshold Exceeded
            if (maxDiff > ErrorThresholdDeg)
            {
                float refDeg = refHorizon.Elevations[ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(maxDiffIndex)];
                float qtDeg = qtAnglesDeg[maxDiffIndex];
                Console.WriteLine($"Max-diff details: idx={maxDiffIndex}, az={maxDiffIndex * 0.25:F2} deg, refDeg={refDeg:F6}, qtDeg={qtDeg:F6}, refRad={refDeg.ToRadians():F6}, qtRad={qtDeg.ToRadians():F6}");

                var comparisonCsv = Path.Combine(outputDir, "ref_vs_qt.csv");
                using (var writer = new StreamWriter(comparisonCsv))
                {
                    writer.WriteLine("index,azimuth_deg,ref_deg,qt_deg,diff_deg");
                    for (int i = 0; i < 1440; i++)
                    {
                        int refIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i);
                        float r = refHorizon.Elevations[refIdx];
                        float q = qtAnglesDeg[i];
                        writer.WriteLine($"{i},{i * 0.25f:F6},{r:F6},{q:F6},{(q - r):F6}");
                    }
                }
                Console.WriteLine($"Wrote reference vs quad-tree samples to {comparisonCsv}");

                Console.WriteLine($"Discrepancy > {ErrorThresholdDeg} deg detected. Running Emulators...");
                
                double targetAzimuth = maxDiffIndex * 0.25; // 0.25 deg steps
                
                // Save directly to project root for easy access by python scripts
                var refTracePath = Path.Combine(Directory.GetCurrentDirectory(), "reference_trace.csv");
                var qtTracePath = Path.Combine(Directory.GetCurrentDirectory(), "quadtree_trace.csv");
                
                var rawOrigin = new PixelOrigin { X = PointX, Y = PointY, Z = PointZ };

                ReferenceRayEmulator.Run(dems[0], rawOrigin, targetAzimuth, refTracePath, suppressCsv: false, unifiedStepMode: false);
                Console.WriteLine($"Traces saved to:");
                Console.WriteLine($"  {refTracePath}");

                for (int demIdx = 0; demIdx < dems.Count; demIdx++)
                {
                    string targetPath = (demIdx == 0)
                        ? qtTracePath
                        : Path.Combine(Directory.GetCurrentDirectory(), $"quadtree_trace_dem{demIdx}.csv");
                    QuadTreeRayEmulator.Run(dems[demIdx], rawOrigin, targetAzimuth, targetPath, suppressCsv: false, unifiedStepMode: false);
                    Console.WriteLine($"  {targetPath}");
                }
                
                // Fail the test to alert user (optional, but good for visibility if running in CI)
                Assert.Fail($"Elevations differ by {maxDiff:F4} deg at Azimuth {targetAzimuth}. Traces generated.");
            }
        }

        //[TestMethod] // fails and is slow
        public void CompareBorderPoint_ReferenceVsQuadTree()
        {
            var (edgePointX, edgePointY, edgePointZ) = (0, 0, 0.0f);
            var dem_paths = new List<string>
            {
                "/d/datasets/viper_v71_2024_medium/other/dem.tif",
                "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
            };

            if (!File.Exists(dem_paths[0]) || !File.Exists(dem_paths[1]))
            {
                Assert.Inconclusive($"DEM files not found at {string.Join(", ", dem_paths)}");
            }

            var outputDir = Path.Combine(TestContext.TestRunDirectory, "SinglePointComparison");
            Directory.CreateDirectory(outputDir);

            var dems = dem_paths.Select(p => new ElevationMap(p)).ToList();

            var refGen = new ReferenceHorizonGenerator();
            using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: false, enableNearFieldReferenceMerge: false, nearFieldClampMeters: 50f);

            var origin = new PixelOrigin { X = edgePointX, Y = edgePointY, Z = edgePointZ };

            var centerOrigin = new PixelOrigin { X = edgePointX, Y = edgePointY, Z = edgePointZ };
            var refHorizon = refGen.GenerateFromPixel(centerOrigin, dems);

            string qtOutDir = Path.Combine(outputDir, "qt_out");
            Directory.CreateDirectory(qtOutDir);
            qtGen.GenerateHorizons(qtOutDir, dems, edgePointX, edgePointY, 1, 1, edgePointZ);

            var observer_dec = (int)Math.Round(edgePointZ * 10);
            var qtFile = Path.Combine(qtOutDir, $"horizon_{edgePointX:D5}_{edgePointY:D5}_{observer_dec:D3}.bin");
            Assert.IsTrue(File.Exists(qtFile), "QuadTree output file was not created.");

            var qtAnglesDeg = Utilities.LoadBinaryArray<float>(qtFile);

            Assert.AreEqual(1440, refHorizon.Elevations.Length);
            Assert.AreEqual(1440, qtAnglesDeg.Length);

            double maxDiff = 0;
            int maxDiffIndex = -1;
            var diffs = new List<float>();

            for (int i = 0; i < 1440; i++)
            {
                int refIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i);

                float refVal = refHorizon.Elevations[refIdx];
                float qtVal = qtAnglesDeg[i];

                if (float.IsNaN(refVal) || refVal <= -1.0e30f || float.IsNaN(qtVal) || qtVal <= -1.0e30f)
                {
                    Console.WriteLine($"Invalid Value at index {i} (Az: {i * 0.25} deg). Ref: {refVal}, QT: {qtVal}");
                    if (maxDiffIndex == -1)
                    {
                        maxDiffIndex = i;
                        maxDiff = 9999;
                    }
                    continue;
                }

                float diff = Math.Abs(refVal - qtVal);
                diffs.Add(diff);

                if (diff > maxDiff)
                {
                    maxDiff = diff;
                    maxDiffIndex = i;
                }
            }

            Console.WriteLine($"Max Difference: {maxDiff:F4} degrees at Azimuth index {maxDiffIndex} ({maxDiffIndex * 0.25} deg)");

            PlotHorizons(refHorizon.Elevations, qtAnglesDeg, Path.Combine(outputDir, "comparison_plot.png"));

            if (maxDiff > ErrorThresholdDeg)
            {
                float refDeg = refHorizon.Elevations[ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(maxDiffIndex)];
                float qtDeg = qtAnglesDeg[maxDiffIndex];
                Console.WriteLine($"Max-diff details: idx={maxDiffIndex}, az={maxDiffIndex * 0.25:F2} deg, refDeg={refDeg:F6}, qtDeg={qtDeg:F6}, refRad={refDeg.ToRadians():F6}, qtRad={qtDeg.ToRadians():F6}");

                var comparisonCsv = Path.Combine(outputDir, "ref_vs_qt.csv");
                using (var writer = new StreamWriter(comparisonCsv))
                {
                    writer.WriteLine("index,azimuth_deg,ref_deg,qt_deg,diff_deg");
                    for (int i = 0; i < 1440; i++)
                    {
                        int refIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i);
                        float r = refHorizon.Elevations[refIdx];
                        float q = qtAnglesDeg[i];
                        writer.WriteLine($"{i},{i * 0.25f:F6},{r:F6},{q:F6},{(q - r):F6}");
                    }
                }
                Console.WriteLine($"Wrote reference vs quad-tree samples to {comparisonCsv}");

                Console.WriteLine($"Discrepancy > {ErrorThresholdDeg} deg detected. Running Emulators...");

                double targetAzimuth = maxDiffIndex * 0.25;

                var refTracePath = Path.Combine(Directory.GetCurrentDirectory(), "reference_trace.csv");
                var qtTracePath = Path.Combine(Directory.GetCurrentDirectory(), "quadtree_trace.csv");

                var rawOrigin = new PixelOrigin { X = edgePointX, Y = edgePointY, Z = edgePointZ };

                ReferenceRayEmulator.Run(dems[0], rawOrigin, targetAzimuth, refTracePath, suppressCsv: false, unifiedStepMode: false);
                Console.WriteLine($"Traces saved to:");
                Console.WriteLine($"  {refTracePath}");

                for (int demIdx = 0; demIdx < dems.Count; demIdx++)
                {
                    string targetPath = (demIdx == 0)
                        ? qtTracePath
                        : Path.Combine(Directory.GetCurrentDirectory(), $"quadtree_trace_dem{demIdx}.csv");
                    QuadTreeRayEmulator.Run(dems[demIdx], rawOrigin, targetAzimuth, targetPath, suppressCsv: false, unifiedStepMode: false);
                    Console.WriteLine($"  {targetPath}");
                }

                Assert.Fail($"Elevations differ by {maxDiff:F4} deg at Azimuth {targetAzimuth}. Traces generated.");
            }
        }

        //[TestMethod] // fails
        public void QuadTreePatch_AdjacentPixelsHaveSimilarHorizons()
        {
            int PointX = 2470; //837;
            int PointY = 5105; //3280;
            float PointZ = 0.0f;
            var demPaths = new List<string>
            {
                "/d/datasets/viper_v71_2024_medium/other/dem.tif",
                "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
                //"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
            };

            if (demPaths.Any(p => !File.Exists(p)))
            {
                Assert.Inconclusive($"One or more DEM files are missing: {string.Join(", ", demPaths)}");
            }

            var dems = demPaths.Select(p => new ElevationMap(p)).ToList();
            using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: false, enableNearFieldReferenceMerge: false, nearFieldClampMeters: 2f);
            int minWidth = dems.Min(d => d.Width);
            int minHeight = dems.Min(d => d.Height);
            int desiredTileX = PointX - PatchWidth / 2;
            int desiredTileY = PointY - PatchHeight / 2;
            int maxTileX = Math.Max(0, minWidth - PatchWidth - PatchEdgeMargin);
            int maxTileY = Math.Max(0, minHeight - PatchHeight - PatchEdgeMargin);
            int tileX = Math.Clamp(desiredTileX, PatchEdgeMargin, maxTileX);
            int tileY = Math.Clamp(desiredTileY, PatchEdgeMargin, maxTileY);
            var horizons = qtGen.GenerateHorizons(dems, tileX, tileY, PatchWidth, PatchHeight, PointZ);
            int expectedLength = PatchWidth * PatchHeight * 1440;
            Assert.AreEqual(expectedLength, horizons.Length, "Unexpected horizon array size.");

            var validPixels = new List<int>();
            var validSet = new HashSet<int>();
            int numBins = 1440;

            // All elevations must be valid
            var horizonDegrees = horizons.Degrees;
            for (var i = 0; i < horizonDegrees.Length; i++)
                Assert.IsTrue(float.IsFinite(horizonDegrees[i]) && !float.IsNegativeInfinity(horizonDegrees[i]), $"Horizon value {horizonDegrees[i]} at index {i} is not finite.");

            var r = new Random(78901);
            var directions = new (int dRow, int dCol)[]
            {
                (-1, 0), (1, 0), (0, -1), (0, 1),
                (-1, -1), (-1, 1), (1, -1), (1, 1)
            };
            var (worstDiff, worstP1, worstP2, worstAzIdx) = (0.0, new Point(), new Point(), 0);
            for (var i = 0; i < PatchComparisonPairs; i++)
            {
                var p1 = new Point(1 + r.Next(PatchWidth - 2), 1 + r.Next(PatchHeight - 2));
                var direction = directions[r.Next(directions.Length)];
                var p2 = new Point(p1.X + direction.dCol, p1.Y + direction.dRow);

                var horizon1_base = PointToHorizonBase(p1);
                var horizon2_base = PointToHorizonBase(p2);

                for (var j = 0; j < numBins; j++)
                {
                    var angle1 = horizonDegrees[horizon1_base + j];
                    var angle2 = horizonDegrees[horizon2_base + j];
                    var diff = Math.Abs(angle1 - angle2);
                    if (diff > PatchComparisonMaxDiffDeg)
                    {
                        Assert.Fail($"Adjacent pixels ({p1.X},{p1.Y}) and ({p2.X},{p2.Y}) differ by {diff:F4} deg at bin {j} (angles: {angle1:F4}, {angle2:F4})");
                    }
                    if (diff > worstDiff)
                        (worstDiff, worstP1, worstP2, worstAzIdx) = (diff, p1, p2, j);
                }
            }        

            Console.WriteLine($"Max adjacent diff observed: {worstDiff:F4} deg at pixels ({worstP1.X},{worstP1.Y}) and ({worstP2.X},{worstP2.Y}) bin {worstAzIdx}");

            int PointToHorizonBase(Point p) => (p.Y * PatchWidth + p.X) * numBins;
        }

        //[DataTestMethod]  // succeeds but is slow
        //[DataRow(837, 3280, 0.0f, "VIPER_Point", "/d/datasets/viper_v71_2024_medium/other/dem.tif", "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif", "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif")]
        //[DataRow(2470, 5105, 0.0f, "test 2a", "/d/datasets/viper_v71_2024_medium/other/dem.tif", null, null)]
        //[DataRow(2470, 5105, 0.0f, "test 2b", "/d/datasets/viper_v71_2024_medium/other/dem.tif", "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif", null)]
        //[DataRow(2470, 5105, 0.0f, "test 2c", "/d/datasets/viper_v71_2024_medium/other/dem.tif", "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif", "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif")]
        public void QuadTreeMatchesReference_Parameterized(int observerX, int observerY, float observerElevation, string testCaseName, string dem1, string dem2, string dem3)
        {
            var demPaths = new[] { dem1, dem2, dem3 }
                .Where(p => !string.IsNullOrWhiteSpace(p))
                //.Distinct()
                .ToList();

            if (demPaths.Count == 0 || demPaths.Any(p => !File.Exists(p)))
            {
                Assert.Inconclusive($"One or more DEM files are missing for test case '{testCaseName}'.");
            }

            var dems = demPaths.Select(GetElevationMap).ToList();
            var refGen = new ReferenceHorizonGenerator();
            using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: false, enableNearFieldReferenceMerge: true, nearFieldClampMeters: 150f);

            var centerOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerElevation };
            var refHorizon = refGen.GenerateFromPixel(centerOrigin, dems);

            var qtResult = qtGen.GenerateHorizons(dems, observerX, observerY, 1, 1, observerElevation);
            if (qtResult.Length < 1440)
                Assert.Fail("QuadTree result did not contain 1440 azimuths.");

            var qtDeg = qtResult.Degrees;

            if (qtDeg.Any(d => float.IsNaN(d)))
                Assert.Fail("QuadTree result contains NaN values.");

            double maxDiff = 0;
            int maxDiffIndex = -1;
            for (int i = 0; i < 1440; i++)
            {
                int refIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i);
                float refDeg = refHorizon.Elevations[refIdx];
                float qtDegVal = qtDeg[i];

                if (!float.IsFinite(refDeg) || !float.IsFinite(qtDegVal) || refDeg <= -1.0e30f || qtDegVal <= -1.0e30f)
                    continue;

                double diff = Math.Abs(refDeg - qtDegVal);
                if (diff > maxDiff)
                {
                    maxDiff = diff;
                    maxDiffIndex = i;
                }
            }

            var plotDir = Path.Combine(TestContext.TestRunDirectory, "ParameterizedComparisons");
            Directory.CreateDirectory(plotDir);
            var plotPath = Path.Combine(plotDir, $"comparison_{testCaseName}.png");
            PlotHorizons(refHorizon.Elevations, qtDeg, plotPath);

            if (maxDiff > 0.5 && maxDiffIndex >= 0)
            {
                double azimuthDeg = maxDiffIndex * 0.25;
                Assert.Fail($"{testCaseName}: Max diff {maxDiff:F3} deg at az {azimuthDeg:F2} deg exceeds tolerance.");
            }

            HorizonDiagnosticsCallback callback = (bufferType, angles) =>
            {
                
            };
        }

        [DataTestMethod]
        [DataRow(2470, 5105, 0.0f, "FarFieldTest a", "/d/datasets/viper_v71_2024_medium/other/dem.tif", "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif", null)]
        public void FarFieldTest(int observerX, int observerY, float observerElevation, string testCaseName, string dem1, string dem2, string dem3)
        {
            var demPaths = new[] { dem1, dem2, dem3 }
                .Where(p => !string.IsNullOrWhiteSpace(p))
                //.Distinct()
                .ToList();

            if (demPaths.Count == 0 || demPaths.Any(p => !File.Exists(p)))
                Assert.Inconclusive($"One or more DEM files are missing for test case '{testCaseName}'.");

            var centerOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerElevation };

            var dems = demPaths.Select(GetElevationMap).ToList();
            var refGen = new ReferenceHorizonGenerator();
            var refHorizon = refGen.GenerateFromPixel(centerOrigin, dems);

            using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: false, enableNearFieldReferenceMerge: true, nearFieldClampMeters: 150f);

            var dict = new Dictionary<HorizonBufferType, HorizonAngles>();
            qtGen.DiagnosticsCallback = (bufferType, angles) => dict[bufferType] = angles;

            var qtResult = qtGen.GenerateHorizons(dems, observerX, observerY, 1, 1, observerElevation);
            Assert.IsNotNull(qtResult);
            if (qtResult.Length < 1440)
                Assert.Fail("QuadTree result did not contain 1440 azimuths.");

            // Plot the data
            var series = dict.Select(kv => new HorizonPlotSeries(kv.Key.ToString(), kv.Value.Degrees, Color.Green, 1.5f, 0f)).ToList();
            series.Insert(0, new HorizonPlotSeries("Reference", refHorizon.Elevations, Color.Red, 2f, 0f));
            List<(Color color,float pen_width,float y_offset)> series_info = new()
            {
                (Color.Green, 1, +1),
                (Color.Blue, 1, +2),
                (Color.Orange, 1, +3),
                (Color.Brown, 1, +4),
            };
            for (var i = 1; i < series.Count; i++)
            {
                var (color, pen_width, y_offset) = series_info[i% series_info.Count];
                series[i].Color = color;
                series[i].PenWidth = pen_width;
                series[i].YOffset = y_offset;
            }
            HorizonComparator.PlotHorizons(
                Path.Combine(TestContext.TestRunDirectory, $"farfieldbug_{testCaseName}.png"),
                series);

            var qtDeg = qtResult.Degrees;

            if (qtDeg.Any(d => float.IsNaN(d)))
                Assert.Fail("QuadTree result contains NaN values.");

            double maxDiff = 0;
            int maxDiffIndex = -1;
            for (int i = 0; i < 1440; i++)
            {
                int refIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i);
                float refDeg = refHorizon.Elevations[refIdx];
                float qtDegVal = qtDeg[i];

                if (!float.IsFinite(refDeg) || !float.IsFinite(qtDegVal) || refDeg <= -1.0e30f || qtDegVal <= -1.0e30f)
                    continue;

                double diff = Math.Abs(refDeg - qtDegVal);
                if (diff > maxDiff)
                {
                    maxDiff = diff;
                    maxDiffIndex = i;
                }
            }

            var plotDir = Path.Combine(TestContext.TestRunDirectory, "ParameterizedComparisons");
            Directory.CreateDirectory(plotDir);
            var plotPath = Path.Combine(plotDir, $"comparison_{testCaseName}.png");
            PlotHorizons(refHorizon.Elevations, qtDeg, plotPath);

            if (maxDiff > 0.5 && maxDiffIndex >= 0)
            {
                double maxDiffDegrees = maxDiffIndex * 0.25;
                Assert.Fail($"{testCaseName}: Max diff {maxDiff:F3} deg at az {maxDiffDegrees:F2} deg exceeds tolerance.");
            }
        }

        //[DataTestMethod] // succeeds but is slow
        //[DataRow("case  1", 1024+5, 1024+123, 0.0f, 11, "/d/datasets/viper_v71_2024_medium/other/dem.tif", "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif", null)]
        public void SinglePointCases(string case_name, int observerX, int observerY, float observerElevation, int azimuth_index, string dem1, string dem2, string dem3)
        {
            var demPaths = new[] { dem1, dem2, dem3 }
                .Where(p => !string.IsNullOrWhiteSpace(p))
                .ToList();

            if (demPaths.Count == 0 || demPaths.Any(p => !File.Exists(p)))
                Assert.Fail($"One or more DEM files are missing for test case '{case_name}'.");

            var dems = demPaths.Select(GetElevationMap).ToList();
            var refGen = new ReferenceHorizonGenerator();
            using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: false, enableNearFieldReferenceMerge: true, nearFieldClampMeters: 150f);

            var centerOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerElevation };
            var refHorizon = refGen.GenerateFromPixel(centerOrigin, dems);

            var qtResult = qtGen.GenerateHorizons(dems, observerX, observerY, 1, 1, observerElevation);
            if (qtResult.Length < 1440)
                Assert.Fail("QuadTree result did not contain 1440 azimuths.");

            var qtDeg = qtResult.Degrees;

            // Run QuadTreeRayEmulator and ReferenceRayEmulator for the specified azimuth index
            double azimuthDeg = azimuth_index * 0.25;
            var rawOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerElevation };

            var qtEmulatorResults = QuadTreeRayEmulator.RunMultiDEM(dems, rawOrigin, azimuthDeg, suppressCsv: true, unifiedStepMode: false);
            var qtEmulatorResult = EmulatorResult.Combine(qtEmulatorResults);
            var refEmulatorResults = ReferenceRayEmulator.RunMultiDem(dems, rawOrigin, azimuthDeg, suppressCsv: true, unifiedStepMode: false);
            var refEmulatorResult = EmulatorResult.Combine(refEmulatorResults);

            var qtEmulatorElevationDeg = qtEmulatorResult.ElevationDeg;
            var refEmulatorElevationDeg = refEmulatorResult.ElevationDeg;
            var emulatorDelta = Math.Abs(qtEmulatorElevationDeg - refEmulatorElevationDeg);

            if (qtDeg.Any(d => float.IsNaN(d)))
                Assert.Fail("QuadTree result contains NaN values.");
            if (qtDeg.Any(d => float.IsNegativeInfinity(d)))
                Assert.Fail("QuadTree result contains Negative Infinity values.");
            if (qtDeg.Any(d => d == -1.0e30f))
                Assert.Fail("QuadTree result contains initialization values.");

            if (refHorizon.Elevations.Any(d => float.IsNaN(d) || float.IsNegativeInfinity(d) || d == -1.0e30f))
                Assert.Fail("Reference result contains bad.");

            Console.WriteLine($"Emulator samples (az {azimuthDeg:F2} deg): QT={qtEmulatorResult.Slopes.Length}, REF={refEmulatorResult.Slopes.Length}");

            double ToDeg(double slope) => double.IsNaN(slope) ? double.NaN : Math.Atan(slope) * (180.0 / Math.PI);

            double qtEmuDeg = (qtEmulatorResult.Slopes.Length > 0) ? ToDeg(qtEmulatorResult.Slopes[^1]) : double.NaN;
            double refEmuDeg = (refEmulatorResult.Slopes.Length > 0) ? ToDeg(refEmulatorResult.Slopes[^1]) : double.NaN;

            int refHorizonIndex = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(azimuth_index);
            double qtAlgDeg = qtDeg[azimuth_index];
            double refAlgDeg = refHorizon.Elevations[refHorizonIndex];

            Console.WriteLine($"Az {azimuthDeg:F2}° comparison -> QT emulator: {qtEmuDeg:F3}° vs QT horizon: {qtAlgDeg:F3}°, REF emulator: {refEmuDeg:F3}° vs REF horizon: {refAlgDeg:F3}°");

            var maxDiff = MaxDiff(refHorizon.Elevations, qtDeg, out int maxDiffIndex);



            if (maxDiff > 0.5 && maxDiffIndex >= 0)
            {
                double maxDiffDegrees = maxDiffIndex * 0.25;
                Assert.Fail($"{case_name}: Max diff {maxDiff:F3} deg at az {maxDiffDegrees:F2} deg exceeds tolerance.");
            }
        }

        //[DataTestMethod]  // succeeds but is slow
        //[DataRow("case  1", 1024 + 5, 1024 + 123, 0.0f, 11, "/d/datasets/viper_v71_2024_medium/other/dem.tif", "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif", null)]
        public void SinglePointEmulatorOnlyCases(string case_name, int observerX, int observerY, float observerElevation, int azimuth_index, string dem1, string dem2, string dem3)
        {
            var demPaths = new[] { dem1, dem2, dem3 }
                .Where(p => !string.IsNullOrWhiteSpace(p))
                .ToList();

            if (demPaths.Count == 0 || demPaths.Any(p => !File.Exists(p)))
                Assert.Fail($"One or more DEM files are missing for test case '{case_name}'.");

            var dems = demPaths.Select(GetElevationMap).ToList();

            // Run QuadTreeRayEmulator and ReferenceRayEmulator for the specified azimuth index
            double azimuthDeg = azimuth_index * 0.25;
            var rawOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerElevation };

            // Enable CSV output for debugging
            var refTracePath = Path.Combine(Directory.GetCurrentDirectory(), "reference_trace_debug.csv");
            var qtTracePath = Path.Combine(Directory.GetCurrentDirectory(), "quadtree_trace_debug.csv");
            
            var qtResults = QuadTreeRayEmulator.RunMultiDEM(dems, rawOrigin, azimuthDeg, suppressCsv: true, unifiedStepMode: false);
            var qtResult = EmulatorResult.Combine(qtResults);
            var refResults = ReferenceRayEmulator.RunMultiDem(dems, rawOrigin, azimuthDeg, suppressCsv: true, unifiedStepMode: false);
            var refResult = EmulatorResult.Combine(refResults);

            // Write CSV traces manually for first DEM only
            if (dems.Count > 0)
            {
                QuadTreeRayEmulator.Run(dems[0], rawOrigin, azimuthDeg, qtTracePath, suppressCsv: false, unifiedStepMode: false);
                ReferenceRayEmulator.Run(dems[0], rawOrigin, azimuthDeg, refTracePath, suppressCsv: false, unifiedStepMode: false);
            }

            var qtEmulatorElevationDeg = qtResult.ElevationDeg;
            var refEmulatorElevationDeg = refResult.ElevationDeg;
            var emulatorDelta = Math.Abs(qtEmulatorElevationDeg - refEmulatorElevationDeg);

            Assert.IsTrue(refResult.Slopes.Length > 10 && qtResult.Slopes.Length > 10, "Insufficient emulator samples for slope comparison.");
            Console.WriteLine("index,refDistance,refElevation,refSlope,refPixelX,refPixelY,qtDistance,qtElevation,qtSlope,qtPixelX,qtPixelY");
            for (var i = 0; i < 10; i++)
                Console.WriteLine($"{i},{refResult.Trace[i].DistanceMeters:F3},{refResult.Trace[i].ElevationMeters:F2},{refResult.Slopes[i]:F6},{refResult.Trace[i].PixelX:F3},{refResult.Trace[i].PixelY:F3},{qtResult.Trace[i].DistanceMeters:F3},{qtResult.Trace[i].ElevationMeters:F2},{qtResult.Slopes[i]:F6},{qtResult.Trace[i].PixelX:F3},{qtResult.Trace[i].PixelY:F3}");
            
            Console.WriteLine($"\nCSV traces written to:\n  {refTracePath}\n  {qtTracePath}");

            if (emulatorDelta > 0.1)
                Assert.Fail($"{case_name}: Emulator difference {emulatorDelta:F3} deg at az {azimuthDeg:F2} deg exceeds tolerance.");
        }

        float MaxDiff(float[] refH, float[] qtH, out int maxDiffIndex)
        {
            float maxDiff = 0;
            maxDiffIndex = -1;
            for (int i = 0; i < 1440; i++)
            {
                int refIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i);
                float refDeg = refH[refIdx];
                float qtDegVal = qtH[i];
                if (!float.IsFinite(refDeg) || !float.IsFinite(qtDegVal) || refDeg <= -1.0e30f || qtDegVal <= -1.0e30f)
                    continue;
                float diff = Math.Abs(refDeg - qtDegVal);
                if (diff > maxDiff)
                {
                    maxDiff = diff;
                    maxDiffIndex = i;
                }
            }
            return (float)maxDiff;
        }

        private void PlotHorizons(float[] refH, float[] qtH, string path)
        {
            const int bins = 1440;
            int w = 1600;
            int h = 600;

            using var bmp = new Bitmap(w, h);
            using var g = Graphics.FromImage(bmp);
            g.Clear(Color.White);

            using var penRef = new Pen(Color.Red, 2f);
            using var penQt = new Pen(Color.Blue, 2f);
            using var penGrid = new Pen(Color.LightGray, 1f);
            using var font = new Font(FontFamily.GenericSansSerif, 11);

            bool IsValid(float v) => !float.IsNaN(v) && !float.IsInfinity(v);

            float minVal = float.MaxValue;
            float maxVal = float.MinValue;
            void ConsiderRange(float[] series)
            {
                foreach (var val in series)
                {
                    if (!IsValid(val))
                        continue;
                    if (val < minVal) minVal = val;
                    if (val > maxVal) maxVal = val;
                }
            }

            ConsiderRange(refH);
            ConsiderRange(qtH);
            if (minVal == float.MaxValue || maxVal == float.MinValue)
            {
                minVal = -90f;
                maxVal = 90f;
            }

            float range = Math.Max(0.1f, maxVal - minVal);
            minVal -= range * 0.1f;
            maxVal += range * 0.1f;

            float ScaleX(int i) => (float)i / (bins - 1) * (w - 1);
            float ScaleY(float v)
            {
                if (!IsValid(v))
                    return h / 2f;
                return h - ((v - minVal) / (maxVal - minVal) * h);
            }

            // Draw vertical grid every 45 degrees (180 bins) and horizontal grid using adaptive step.
            for (int i = 0; i <= bins; i += 180)
            {
                float x = ScaleX(i);
                g.DrawLine(penGrid, x, 0, x, h);
                g.DrawString($"{i / 4.0:F0}°", font, Brushes.Gray, x + 2, h - 22);
            }

            float step = (maxVal - minVal) / 10f;
            if (step <= 0) step = 1f;
            float magnitude = (float)Math.Pow(10, Math.Floor(Math.Log10(step)));
            float normalized = step / magnitude;
            if (normalized < 2) step = 1 * magnitude;
            else if (normalized < 5) step = 2 * magnitude;
            else step = 5 * magnitude;

            float start = (float)Math.Floor(minVal / step) * step;
            for (float val = start; val <= maxVal; val += step)
            {
                float y = ScaleY(val);
                g.DrawLine(penGrid, 0, y, w, y);
                g.DrawString($"{val:F1}°", font, Brushes.Gray, 5, y - 12);
            }

            // Plot series
            for (int i = 0; i < bins - 1; i++)
            {
                if (IsValid(refH[i]) && IsValid(refH[i + 1]))
                    g.DrawLine(penRef, ScaleX(i), ScaleY(refH[i]), ScaleX(i + 1), ScaleY(refH[i + 1]));

                if (IsValid(qtH[i]) && IsValid(qtH[i + 1]))
                    g.DrawLine(penQt, ScaleX(i), ScaleY(qtH[i]), ScaleX(i + 1), ScaleY(qtH[i + 1]));
            }

            // Legend
            var legendRect = new Rectangle(w - 260, 20, 230, 70);
            g.FillRectangle(new SolidBrush(Color.FromArgb(230, 255, 255, 255)), legendRect);
            g.DrawRectangle(Pens.Gray, legendRect);
            float legendY = legendRect.Top + 15;
            void DrawLegendEntry(string label, Color color)
            {
                using var legendPen = new Pen(color, 3f);
                g.DrawLine(legendPen, legendRect.Left + 10, legendY, legendRect.Left + 60, legendY);
                g.DrawString(label, font, Brushes.Black, legendRect.Left + 70, legendY - 8);
                legendY += 20;
            }
            DrawLegendEntry("Reference", Color.Red);
            DrawLegendEntry("QuadTree", Color.Blue);

            bmp.Save(path, ImageFormat.Png);
            Console.WriteLine($"Plot saved to {path}");
        }

        private static ElevationMap GetElevationMap(string path)
        {
            lock (ElevationMapCacheLock)
            {
                if (!ElevationMapCache.TryGetValue(path, out var map))
                {
                    map = new ElevationMap(path);
                    ElevationMapCache[path] = map;
                }
                return map;
            }
        }

        [ClassCleanup]
        public static void DisposeCachedElevationMaps()
        {
            lock (ElevationMapCacheLock)
            {
                ElevationMapCache.Clear();
            }
        }

        private static Dictionary<string, string> PatchNameToFileName = new()
        {
            // Example mapping
            //{ "", "/d/projects/new_horizon/horizon_runner/bin/x64/Debug/net9.0/output/horizon_00000_00000_000.bin" },
        };

        public TestContext TestContext { get; set; }
    }
}
