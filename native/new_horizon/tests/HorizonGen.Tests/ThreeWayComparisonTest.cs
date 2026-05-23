using moonlib.horizon;
using moonlib.util;
using System.Drawing;

#nullable disable

namespace moonlib.tests
{
    [TestClass]
    public class ThreeWayComparisonTest
    {
        private const string InnerDemPath = @"/d/datasets/viper_v71_2024_medium/other/dem.tif";
        private const string OuterDemPath = @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif";
        private const string SouthPoleDemPath = @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif";

        public TestContext TestContext { get; set; }

        [TestMethod]
        public void CompareAllThreeApproaches_SinglePixel()
        {
            // The purpose of this test is to compare QTGPU (the production code) with the reference horizon generator.
            // The horizons should agree closely.  These are requirements:
            // 1. QTGPU should match Reference within 1.0 degree at all azimuths.
            // 2. The use of two ElevationMaps in this test with different projections is intentional.  The QT and Reference
            //    algorithms are required to handle multiple DEMs with different projections.
            // The QT-Emulator is also run for additional information.

            // Test pixel
            int pixelX = 702;
            int pixelY = 4736;
            float observerElevation = 0.0f;

            if (!File.Exists(InnerDemPath))
            {
                Assert.Inconclusive($"Inner DEM not found: {InnerDemPath}");
            }
            if (!File.Exists(OuterDemPath))
            {
                Assert.Inconclusive($"Outer DEM not found: {OuterDemPath}");
            }

            var dems = new List<ElevationMap>
            {
                new ElevationMap(InnerDemPath),
                new ElevationMap(OuterDemPath),
                new ElevationMap(SouthPoleDemPath)
            };

            var origin = new PixelOrigin { X = pixelX, Y = pixelY, Z = observerElevation };
            var originWithOffset = new PixelOrigin { X = pixelX, Y = pixelY, Z = observerElevation };

            Console.WriteLine($"Testing pixel ({pixelX}, {pixelY}) with observer elevation {observerElevation}m");
            Console.WriteLine($"Inner DEM: {InnerDemPath}");
            Console.WriteLine($"Outer DEM: {OuterDemPath}");

            // ========================================
            // 1. Reference Generator (Full Horizon)
            // ========================================
            Console.WriteLine("\n=== Running Reference Generator ===");
            var refGen = new ReferenceHorizonGenerator();
            var refResult = refGen.GenerateFromPixel(originWithOffset, dems);
            var refAngles = refResult.Elevations; // 1440 angles in degrees

            Console.WriteLine($"Reference: Generated {refAngles.Length} angles");
            Console.WriteLine($"  Min angle: {refAngles.Min():F3}°");
            Console.WriteLine($"  Max angle: {refAngles.Max():F3}°");
            Console.WriteLine($"  Mean angle: {refAngles.Average():F3}°");

            // ========================================
            // 2. QuadTree GPU Generator (Full Horizon)
            // ========================================
            Console.WriteLine("\n=== Running QuadTree GPU Generator ===");
            using var qtGen = new QuadTreeHorizonGenerator(
                disableHierarchy: true,
                enableNearFieldReferenceMerge: false,
                nearFieldClampMeters: 0f);

            // Generate for 1x1 tile at the target pixel
            var qtResult = qtGen.GenerateHorizons(
                dems,
                tileX: pixelX,
                tileY: pixelY,
                width: 1,
                height: 1,
                observerElevation: observerElevation);

            var qtAngles = qtResult.Degrees; // Should be 1440 angles
            Console.WriteLine($"QuadTree GPU: Generated {qtAngles.Length} angles");
            Console.WriteLine($"  Min angle: {qtAngles.Min():F3}°");
            Console.WriteLine($"  Max angle: {qtAngles.Max():F3}°");
            Console.WriteLine($"  Mean angle: {qtAngles.Average():F3}°");

            // ========================================
            // 3. Run QuadTree Emulator for Full Horizon (1440 angles)
            // ========================================
            Console.WriteLine("\n=== Running QuadTree Emulator for Full Horizon ===");
            var qtEmulAngles = new float[1440];

            //var azimuth_index_enumeration = Enumerable.Range(0, 1440);
            var azimuth_index_enumeration = Enumerable.Range(0, 1440).Skip(90 * 4).Take(1);

            foreach (var azIdx in azimuth_index_enumeration)
            {
                double azimuthDeg = azIdx * 0.25; // 0.25 degree resolution

                var qtEmulResult = QuadTreeRayEmulator.RunMultiDEM(
                    dems,
                    origin,
                    azimuthDeg,
                    maxDistanceMeters: 1000000.0,
                    suppressCsv: true,
                    unifiedStepMode: false);

                float qtEmulMaxSlope = float.MinValue;
                foreach (var demResult in qtEmulResult)
                {
                    if (demResult.Slopes.Length > 0)
                    {
                        qtEmulMaxSlope = Math.Max(qtEmulMaxSlope, (float)demResult.Slopes.Max());
                    }
                }
                qtEmulAngles[azIdx] = (float)(Math.Atan(qtEmulMaxSlope) * 180.0 / Math.PI);
            }

            Console.WriteLine($"QuadTree Emulator: Generated {qtEmulAngles.Length} angles");
            Console.WriteLine($"  Min angle: {qtEmulAngles.Min():F3}°");
            Console.WriteLine($"  Max angle: {qtEmulAngles.Max():F3}°");
            Console.WriteLine($"  Mean angle: {qtEmulAngles.Average():F3}°");

            // ========================================
            // 4. Sample Multiple Azimuths with Both Emulators for Detailed Comparison
            // ========================================
            Console.WriteLine("\n=== Running Both Emulators for Sample Azimuths ===");

            // Test every 10 degrees (36 samples)
            var testAzimuths = new List<double>();
            for (int az = 0; az < 360; az += 10)
            {
                testAzimuths.Add(az);
            }

            testAzimuths = new List<double> { 90 };

            var emulatorComparisons = new List<(double azimuth, float refAngle, float qtGpuAngle, float qtEmulAngle, float refEmulAngle)>();

            foreach (var azimuthDeg in testAzimuths)
            {
                // Get GPU result for this azimuth (convert azimuth to index)
                int azIndex = (int)Math.Round(azimuthDeg / 0.25) % 1440;
                float refAngle = refAngles[azIndex];
                float qtGpuAngle = qtAngles[azIndex];

                // Run QuadTree Emulator
                var qtEmulResult = QuadTreeRayEmulator.RunMultiDEM(
                    dems,
                    origin,
                    azimuthDeg,
                    maxDistanceMeters: 1000000.0,
                    suppressCsv: false,
                    unifiedStepMode: false);

                float qtEmulMaxSlope = float.MinValue;
                foreach (var demResult in qtEmulResult)
                {
                    if (demResult.Slopes.Length > 0)
                    {
                        qtEmulMaxSlope = Math.Max(qtEmulMaxSlope, (float)demResult.Slopes.Max());
                    }
                }
                float qtEmulAngle = (float)(Math.Atan(qtEmulMaxSlope) * 180.0 / Math.PI);

                // Run Reference Emulator
                var refEmulResult = ReferenceRayEmulator.RunMultiDem(
                    dems,
                    origin,
                    azimuthDeg,
                    maxDistanceMeters: 100000.0,
                    suppressCsv: true,
                    unifiedStepMode: false);

                float refEmulMaxSlope = float.MinValue;
                foreach (var demResult in refEmulResult)
                {
                    if (demResult.Slopes.Length > 0)
                    {
                        refEmulMaxSlope = Math.Max(refEmulMaxSlope, (float)demResult.Slopes.Max());
                    }
                }
                float refEmulAngle = (float)(Math.Atan(refEmulMaxSlope) * 180.0 / Math.PI);

                emulatorComparisons.Add((azimuthDeg, refAngle, qtGpuAngle, qtEmulAngle, refEmulAngle));
            }

            // ========================================
            // 4. Report Results
            // ========================================
            Console.WriteLine("\n=== Detailed Comparison (Sample Azimuths) ===");
            Console.WriteLine("Azimuth | RefGen | QtGPU  | QtEmul | RefEmul | GPU-Ref | GPU-Emul");
            Console.WriteLine("--------|--------|--------|--------|---------|---------|---------");

            var maxGpuRefDiff = 0.0;
            var maxGpuEmulDiff = 0.0;

            foreach (var (az, refAngle, qtGpuAngle, qtEmulAngle, refEmulAngle) in emulatorComparisons)
            {
                var gpuRefDiff = Math.Abs(qtGpuAngle - refAngle);
                var gpuEmulDiff = Math.Abs(qtGpuAngle - qtEmulAngle);

                maxGpuRefDiff = Math.Max(maxGpuRefDiff, gpuRefDiff);
                maxGpuEmulDiff = Math.Max(maxGpuEmulDiff, gpuEmulDiff);

                Console.WriteLine($"{az,7:F1} | {refAngle,6:F2} | {qtGpuAngle,6:F2} | {qtEmulAngle,6:F2} | {refEmulAngle,6:F2} | " +
                                $"{gpuRefDiff,7:F3} | {gpuEmulDiff,8:F3}");
            }

            // ========================================
            // 5. Summary Statistics
            // ========================================
            Console.WriteLine("\n=== Summary Statistics ===");
            Console.WriteLine($"Max difference (GPU vs Reference):        {maxGpuRefDiff:F3}°");
            Console.WriteLine($"Max difference (GPU vs QtEmulator):       {maxGpuEmulDiff:F3}°");

            // Calculate full horizon differences
            var fullHorizonDiffs = new double[1440];
            for (int i = 0; i < 1440; i++)
            {
                fullHorizonDiffs[i] = Math.Abs(qtAngles[i] - refAngles[i]);
            }

            Console.WriteLine($"\nFull Horizon (1440 angles):");
            Console.WriteLine($"  Mean difference:  {fullHorizonDiffs.Average():F3}°");
            Console.WriteLine($"  Max difference:   {fullHorizonDiffs.Max():F3}°");
            Console.WriteLine($"  Median difference: {fullHorizonDiffs.OrderBy(x => x).ElementAt(720):F3}°");
            Console.WriteLine($"  95th percentile:  {fullHorizonDiffs.OrderBy(x => x).ElementAt(1368):F3}°");

            // ========================================
            // 6. Plot the horizons for visual inspection
            // ========================================
            var outputPath = Path.Combine(TestContext.TestRunDirectory, "ThreeWayComparison_Pixel1024x1025.png");
            var plotSeries = new List<HorizonPlotSeries>
            {
                new HorizonPlotSeries("Reference", refAngles, Color.Red, 2f, 0f),
                new HorizonPlotSeries("QuadTree Emulator", qtEmulAngles, Color.Blue, 2f, 0f),
                new HorizonPlotSeries("QuadTree GPU", qtAngles, Color.Green, 2f, 0f),
            };

            HorizonComparator.PlotHorizons(outputPath, plotSeries);
            Console.WriteLine($"\nPlot saved to: {outputPath}");

            // ========================================
            // 7. Assertions
            // ========================================

            // The GPU should match the reference (ultimate goal)
            // Relaxed to 4.0 due to known polynomial vs geodesic deviation
            Assert.IsTrue(fullHorizonDiffs.Max() < 1.0,
                $"GPU differs from Reference by more than 1.0°: {fullHorizonDiffs.Max():F3}°");

            // The GPU should match the emulator (if not, the GPU has a bug)
            Assert.IsTrue(maxGpuEmulDiff < 1.0,
                $"GPU differs from its own emulator by more than 1°: {maxGpuEmulDiff:F3}°. This suggests a bug in the GPU implementation.");
        }

        //[TestMethod]  // fails and is slow
        public void CompareAllThreeApproaches_SinglePixelV2()
        {
            // The purpose of this test is to compare QTGPU (the production code) with the reference horizon generator.
            // The horizons should agree closely.  These are requirements:
            // 1. QTGPU should match Reference within 1.0 degree at all azimuths.
            // 2. The use of two ElevationMaps in this test with different projections is intentional.  The QT and Reference
            //    algorithms are required to handle multiple DEMs with different projections.
            // The QT-Emulator is also run for additional information.

            // Print the test context directory
            Console.WriteLine($"Test Context Directory: {Path.GetFullPath(TestContext.TestRunDirectory)}");

            // Test pixel
            int pixelX = 1009;
            int pixelY = 372;
            float observerElevation = 0.0f;
            float azimuthDeg = 225;
            int azIndex = (int)Math.Round(azimuthDeg / 0.25) % 1440;

            var dems = new List<ElevationMap>
            {
                new ElevationMap(InnerDemPath),
                new ElevationMap(OuterDemPath),
                new ElevationMap(SouthPoleDemPath)
            };

            var origin = new PixelOrigin { X = pixelX, Y = pixelY, Z = observerElevation };
            var originWithOffset = new PixelOrigin { X = pixelX, Y = pixelY, Z = observerElevation };

            Console.WriteLine($"Testing pixel ({pixelX}, {pixelY}) with observer elevation {observerElevation}m");
            Console.WriteLine($"Azimuth={azimuthDeg}  azIndex={azIndex}");
            Console.WriteLine($"Inner DEM: {InnerDemPath}");
            Console.WriteLine($"Outer DEM: {OuterDemPath}");

            //

            var newHorizonIndex = new HorizonFileIndex(@"/d/projects/new_horizon/output_horizons", 0f);
            var newLoadedHorizon = newHorizonIndex.LoadHorizon(pixelX, pixelY);
            Assert.IsNotNull(newLoadedHorizon, "Failed to load new horizon data.");
            var newLoadedElevation = newLoadedHorizon[azIndex];

            var oldHorizonIndex = new HorizonFileIndex(@"/d/datasets/viper_v71_2025_medium/horizon", 0f);
            var oldLoadedHorizon = oldHorizonIndex.LoadHorizon(pixelX, pixelY);
            Assert.IsNotNull(oldLoadedHorizon, "Failed to load old horizon data.");
            var oldLoadedElevation = oldLoadedHorizon[azIndex];

            // ========================================
            // 1. Reference Generator (Full Horizon)
            // ========================================
            Console.WriteLine("\n=== Running Reference Generator ===");
            var refGen = new ReferenceHorizonGenerator();
            var refResult = refGen.GenerateFromPixel(originWithOffset, dems);
            var refAngles = refResult.Elevations; // 1440 angles in degrees

            // ========================================
            // 2. QuadTree GPU Generator (Full Horizon)
            // ========================================
            Console.WriteLine("\n=== Running QuadTree GPU Generator ===");
            using var qtGen = new QuadTreeHorizonGenerator(
                disableHierarchy: true,
                enableNearFieldReferenceMerge: false,
                nearFieldClampMeters: 0f);

            // Generate for 1x1 tile at the target pixel
            var qtResult = qtGen.GenerateHorizons(
                dems,
                tileX: pixelX,
                tileY: pixelY,
                width: 1,
                height: 1,
                observerElevation: observerElevation);
            var qtAngles = qtResult.Degrees; // Should be 1440 angles

            // Get GPU result for this azimuth (convert azimuth to index)
            float refAngle = refAngles[azIndex];
            float qtGpuAngle = qtAngles[azIndex];

            // ========================================
            // 3. Run Both Emulators for Detailed Comparison
            // ========================================

            // Run QuadTree Emulator
            var qtEmulResult = QuadTreeRayEmulator.RunMultiDEM(
                dems,
                origin,
                azimuthDeg,
                maxDistanceMeters: 1000000.0,
                suppressCsv: false,
                unifiedStepMode: false);

            var qtEmulAngle = qtEmulResult.Max(s => s.ElevationDeg);

            // Run Reference Emulator
            var refEmulResult = ReferenceRayEmulator.RunMultiDem(
                dems,
                origin,
                azimuthDeg,
                maxDistanceMeters: 100000.0,
                suppressCsv: true,
                unifiedStepMode: false);

            float refEmulAngle = refEmulResult.Max(s => s.ElevationDeg);

            // ========================================
            // 4. Report Results
            // ========================================

            var delta_qtgpu_ref = Math.Abs(qtGpuAngle - refAngle);
            var delta_qtemu_refemu = Math.Abs(qtEmulAngle - refEmulAngle);
            var delta_qtgpu_qtemu = Math.Abs(qtGpuAngle - qtEmulAngle);
            var delta_new_loaded = Math.Abs(qtGpuAngle - newLoadedElevation);

            Console.WriteLine($"oldLoadedElevation ={oldLoadedElevation}");
            Console.WriteLine($"newLoadedElevation ={newLoadedElevation}");
            Console.WriteLine($"qtGpuAngle         ={qtGpuAngle}");
            Console.WriteLine($"qtEmulAngle        ={qtEmulAngle}");
            Console.WriteLine($"refAngle           ={refAngle}");
            Console.WriteLine($"refEmulAngle       ={refEmulAngle}");
            Console.WriteLine($"delta_qtemu_refemu ={delta_qtemu_refemu}");
            Console.WriteLine($"delta_qtgpu_qtemu  ={delta_qtgpu_qtemu}");
            Console.WriteLine($"delta_qtgpu_ref    ={delta_qtgpu_ref}");

            var allowable_error = 0.15f;

            // The GPU should match the reference (ultimate goal)
            Assert.IsTrue(delta_qtgpu_ref <= allowable_error, $"GPU differs from Reference by more than {allowable_error}°: {delta_qtgpu_ref:F3}°");

            Assert.IsTrue(delta_new_loaded >= allowable_error, $"GPU differs from previously generated by more than {allowable_error}°: {delta_qtgpu_ref:F3}");

            // The GPU should match the emulator (if not, the GPU has a bug)
            Assert.IsTrue(delta_qtgpu_qtemu <= allowable_error, $"GPU differs from its own emulator by more than {allowable_error}°: {delta_qtgpu_qtemu:F3}°.");
        }
    }
}
