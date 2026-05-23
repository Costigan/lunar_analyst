using Microsoft.VisualStudio.TestTools.UnitTesting;
using moonlib.horizon;
using moonlib.math;
using System.Text.Json;

namespace HorizonGen.Tests
{
    [TestClass]
    public class TestCaseReplayTests
    {
        // Define DTOs to match the JSON structure exported by CompareHorizons
        public class TestCaseData
        {
            public DateTime Timestamp { get; set; }
            public ObserverPosData? Observer { get; set; }
            public float Azimuth { get; set; }
            public string? LoadedHorizonFile { get; set; }
        }

        public class ObserverPosData
        {
            public int X { get; set; }
            public int Y { get; set; }
            public float Z { get; set; }
            public override string ToString() => $"({X}, {Y}, {Z})";
        }

        private static readonly Dictionary<string, ElevationMap> ElevationMapCache = new(StringComparer.OrdinalIgnoreCase);
        private static readonly object ElevationMapCacheLock = new();

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

        //[DataTestMethod]
        //[DataRow("/d/projects/new_horizon/CompareHorizons/bin/x64/Debug/net9.0-windows/discrepancy_report_2026-01-29_14-46-11.json")]
        //[DataRow("/d/projects/new_horizon/CompareHorizons/bin/x64/Debug/net9.0-windows/discrepancy_report_2026-01-29_17-15-53.json")] 
        //[DataRow("/d/projects/new_horizon/CompareHorizons/bin/x64/Debug/net9.0-windows/discrepancy_report_2026-02-01_18-40-34.json")] 
        //[DataRow("/d/projects/new_horizon/CompareHorizons/bin/x64/Debug/net9.0-windows/discrepancy_report_2026-02-04_11-02-36.json")]
        public void ReplayTestCase(string testCaseFilePath)
        {
            if (!File.Exists(testCaseFilePath))
            {
                Assert.Inconclusive($"Test case file not found: {testCaseFilePath}");
            }

            // 1. Deserialize Test Case
            string json = File.ReadAllText(testCaseFilePath);
            var options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
            var data = JsonSerializer.Deserialize<TestCaseData>(json, options);

            Assert.IsNotNull(data, "Failed to deserialize test case data.");
            Assert.IsNotNull(data.Observer, "Observer data is missing in test case.");

            int observerX = data.Observer.X;
            int observerY = data.Observer.Y;
            float observerZ = data.Observer.Z;
            double azimuthDeg = data.Azimuth;

            Console.WriteLine($"Replaying Test Case: {Path.GetFileName(testCaseFilePath)}");
            Console.WriteLine($"Observer: ({observerX}, {observerY}, {observerZ})");
            Console.WriteLine($"Azimuth: {azimuthDeg:F2} deg");

            // 2. Setup DEMs
            // Using standard test paths. In a real scenario, we might want to infer these or pass them in.
            var demPaths = new[] {
                @"/d/datasets/viper_v71_2024_medium/other/dem.tif",
                @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
                @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
            };

            var validDemPaths = demPaths.Where(File.Exists).ToList();
            if (validDemPaths.Count == 0)
            {
                Assert.Inconclusive("No valid DEM files found. Cannot run replay.");
            }
            var dems = validDemPaths.Select(GetElevationMap).ToList();

            // 3. Generate Horizons (Full 360)
            var refGen = new ReferenceHorizonGenerator();
            // Match configuration from SinglePointComparisonTests
            using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: false, enableNearFieldReferenceMerge: true, nearFieldClampMeters: 150f);

            // Ref Gen (uses center of pixel)
            var centerOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerZ };

            Console.WriteLine($"Generating a reference horizon. origin={data.Observer}");
            Console.WriteLine($"Using DEMs: {string.Join(", ", dems.Select(d => d.Path))}");
            var refHorizon = refGen.GenerateFromPixel(centerOrigin, dems);

            // QT Gen
            // GenerateHorizons returns HorizonAngles (degrees)
            var qtResult = qtGen.GenerateHorizons(dems, observerX, observerY, 1, 1, observerZ);
            
            Assert.IsNotNull(qtResult.Degrees, "QuadTree generation failed.");
            Assert.AreEqual(1440, qtResult.Degrees.Length, "QuadTree horizon length mismatch.");
            Assert.AreEqual(1440, refHorizon.Elevations.Length, "Reference horizon length mismatch.");

            // Find the maxima and index of the maxima of the two horizons.
            var (refMaxIndex, refMax) = Enumerable.Range(0, 1440).Select(i => (i, refHorizon.Elevations[i])).MaxBy(pair => pair.Item2);
            var (qtMaxIndex, qtMax) = Enumerable.Range(0, 1440).Select(i => (i, qtResult.Degrees[i])).MaxBy(pair => pair.Item2);
            var refMaxIndex2 = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(refMaxIndex);

            Console.WriteLine($"Reference Horizon Max: {refMax:F4} deg at Index {refMaxIndex} ({(refMaxIndex * 0.25):F2} deg)");

            // 4. Compare Horizons (All Azimuths)
            float maxHorizonDiff = 0f;
            int maxHorizonDiffIndex = -1;

            for (int i = 0; i < 1440; i++)
            {
                int refIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i);
                float refVal = refHorizon.Elevations[refIdx];
                float qtVal = qtResult.Degrees[i];

                if (float.IsFinite(refVal) && float.IsFinite(qtVal))
                {
                    float diff = Math.Abs(refVal - qtVal);
                    if (diff > maxHorizonDiff)
                    {
                        maxHorizonDiff = diff;
                        maxHorizonDiffIndex = i;
                    }
                }
            }

            Console.WriteLine($"Max Horizon Discrepancy: {maxHorizonDiff:F4} deg at Azimuth Index {maxHorizonDiffIndex} ({(maxHorizonDiffIndex * 0.25):F2} deg)");

            double targetAzimuthDeg = maxHorizonDiffIndex * 0.25;
            Console.WriteLine($"Running emulators at max discrepancy azimuth: {targetAzimuthDeg:F2} deg");

            // 5. Run Emulators (At Max Discrepancy Azimuth)
            var rawOrigin = new PixelOrigin { X = observerX, Y = observerY, Z = observerZ };
            
            var qtEmuResults = QuadTreeRayEmulator.RunMultiDEM(dems, rawOrigin, targetAzimuthDeg, suppressCsv: true, unifiedStepMode: false);
            var qtEmuResult = EmulatorResult.Combine(qtEmuResults);

            var refEmuResults = ReferenceRayEmulator.RunMultiDem(dems, rawOrigin, targetAzimuthDeg, suppressCsv: true, unifiedStepMode: false);
            var refEmuResult = EmulatorResult.Combine(refEmuResults);

            double qtEmuDeg = qtEmuResult.ElevationDeg;
            double refEmuDeg = refEmuResult.ElevationDeg;
            
            // Get full horizon values at this azimuth
            int targetRefIdx = ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(maxHorizonDiffIndex);
            double refHorizonDeg = refHorizon.Elevations[targetRefIdx];
            double qtHorizonDeg = qtResult.Degrees[maxHorizonDiffIndex];

            double emuDiff = Math.Abs(qtEmuDeg - refEmuDeg);
            double qtConsistencyDiff = Math.Abs(qtEmuDeg - qtHorizonDeg);
            double refConsistencyDiff = Math.Abs(refEmuDeg - refHorizonDeg);

            Console.WriteLine($"Analysis for Azimuth {targetAzimuthDeg:F2}:");
            Console.WriteLine($"  QT Horizon:   {qtHorizonDeg:F4} deg");
            Console.WriteLine($"  Ref Horizon:  {refHorizonDeg:F4} deg");
            Console.WriteLine($"  QT Emulator:  {qtEmuDeg:F4} deg");
            Console.WriteLine($"  Ref Emulator: {refEmuDeg:F4} deg");
            Console.WriteLine($"Deltas:");
            Console.WriteLine($"  Horizon (QT vs Ref):      {maxHorizonDiff:F4} deg");
            Console.WriteLine($"  Emulator (QT vs Ref):     {emuDiff:F4} deg");
            Console.WriteLine($"  QT Consistency (H vs E):  {qtConsistencyDiff:F4} deg");
            Console.WriteLine($"  Ref Consistency (H vs E): {refConsistencyDiff:F4} deg");

            // 6. Assertions
            if (maxHorizonDiff > 0.1f)
                Assert.Fail($"Horizon discrepancy too large: {maxHorizonDiff:F4} deg at index {maxHorizonDiffIndex} ({targetAzimuthDeg:F2} deg).");
            
            if (emuDiff > 0.1f)
                Assert.Fail($"Emulator discrepancy too large: {emuDiff:F4} deg at azimuth {targetAzimuthDeg:F2}.");

            if (qtConsistencyDiff > 0.1f)
                Assert.Fail($"QT Horizon vs Emulator consistency failure: {qtConsistencyDiff:F4} deg at azimuth {targetAzimuthDeg:F2}.");

            if (refConsistencyDiff > 0.1f)
                Assert.Fail($"Ref Horizon vs Emulator consistency failure: {refConsistencyDiff:F4} deg at azimuth {targetAzimuthDeg:F2}.");
        }
    }
}
