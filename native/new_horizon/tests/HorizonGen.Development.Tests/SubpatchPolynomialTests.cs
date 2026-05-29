using Microsoft.VisualStudio.TestTools.UnitTesting;
using moonlib;
using moonlib.horizon;
using System;
using System.IO;
using System.Linq;

namespace moonlib.tests
{
    /// <summary>
    /// Tests for the subpatch polynomial approach to improve horizon accuracy
    /// </summary>
    [TestClass]
    public class SubpatchPolynomialTests
    {
        private static readonly string[] DemPaths = new[] {
            @"/d/datasets/viper_v71_2024_medium/other/dem.tif",
            @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
            @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
        };

        [TestMethod]
        public void TestSubpatchPolynomialAccuracy()
        {
            // Skip test if DEMs don't exist
            if (!DemPaths.All(File.Exists))
            {
                Assert.Inconclusive($"Required DEMs not found. Expected: {string.Join(", ", DemPaths)}");
                return;
            }

            // Test parameters - same location as the scaling test that showed large errors
            const int targetPixelX = 2048;
            const int targetPixelY = 128;
            const float observerElevation = 0.0f;
            const int targetAzimuthDegrees = 55;
            const int targetAzimuthIndex = (int)((targetAzimuthDegrees / 360.0) * 1440);

            Console.WriteLine($"Testing subpatch polynomial approach at pixel ({targetPixelX}, {targetPixelY})");
            Console.WriteLine($"Target azimuth: {targetAzimuthDegrees}° (bin {targetAzimuthIndex})");

            try
            {
                // Load DEMs
                var dems = DemPaths.Select(path => new ElevationMap(path)).ToList();
                Console.WriteLine($"Loaded {dems.Count} DEMs");

                using (var generator = new QuadTreeHorizonGenerator())
                {
                    // Generate ground truth: 1x1 patch at target location
                    Console.WriteLine("=== Ground Truth (1x1 patch) ===");
                    var groundTruth = generator.GenerateHorizons(dems, targetPixelX, targetPixelY, 1, 1, observerElevation);
                    float groundTruthValue = groundTruth.Degrees[targetAzimuthIndex];
                    Console.WriteLine($"Ground truth horizon at {targetAzimuthDegrees}°: {groundTruthValue:F6}°");

                    // Generate with standard 128x128 approach (should show large error)
                    Console.WriteLine("\n=== Standard 128x128 Compact Mode ===");
                    var standard128 = generator.GenerateHorizons(dems, targetPixelX, targetPixelY, 128, 128, observerElevation);
                    float standard128Value = standard128.Degrees[targetAzimuthIndex];
                    float standard128Error = Math.Abs(standard128Value - groundTruthValue);
                    Console.WriteLine($"Standard 128x128 horizon: {standard128Value:F6}°");
                    Console.WriteLine($"Standard 128x128 error: {standard128Error:F6}°");

                    // Test different subpatch sizes
                    int[] subpatchSizes = { 64, 32, 16, 8 };
                    Console.WriteLine("\n=== Subpatch Polynomial Results ===");
                    Console.WriteLine("SubpatchSize | Target Error | Max Error | Expected Distance | Status");
                    Console.WriteLine("-------------|--------------|-----------|-------------------|--------");

                    foreach (int subpatchSize in subpatchSizes)
                    {
                        try
                        {
                            var subpatchResult = generator.GenerateHorizonsWithSubpatches(dems, targetPixelX, targetPixelY, 128, 128, observerElevation, subpatchSize);
                            float subpatchValue = subpatchResult.Degrees[targetAzimuthIndex];
                            float targetError = Math.Abs(subpatchValue - groundTruthValue);

                            // Calculate expected max distance from subpatch center for corner pixel
                            double expectedMaxDistance = Math.Sqrt(Math.Pow(subpatchSize / 2.0, 2) + Math.Pow(subpatchSize / 2.0, 2));

                            // Calculate maximum error across all azimuth bins
                            float maxError = 0;
                            for (int azIdx = 0; azIdx < 1440; azIdx++)
                            {
                                float error = Math.Abs(subpatchResult.Degrees[azIdx] - groundTruth.Degrees[azIdx]);
                                if (error > maxError)
                                    maxError = error;
                            }

                            string status = targetError < 0.05f ? "✅ GOOD" : (targetError < 0.1f ? "⚠️ OK" : "❌ POOR");
                            
                            Console.WriteLine($"{subpatchSize,12} | {targetError,11:F6}° | {maxError,9:F6}° | {expectedMaxDistance,15:F1} pixels | {status}");
                        }
                        catch (Exception ex)
                        {
                            Console.WriteLine($"{subpatchSize,12} | ERROR: {ex.Message}");
                        }
                    }

                    Console.WriteLine("\n=== Summary ===");
                    Console.WriteLine($"Ground truth (1x1): {groundTruthValue:F6}°");
                    Console.WriteLine($"Standard (128x128): {standard128Value:F6}° (error: {standard128Error:F6}°)");
                    Console.WriteLine("Subpatches should show significantly reduced errors as subpatch size decreases");
                    Console.WriteLine("Expected: 32x32 subpatches should have <0.1° errors, 16x16 should have <0.05° errors");
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Test failed with exception: {ex.Message}");
                Console.WriteLine($"Stack trace: {ex.StackTrace}");
                throw;
            }
        }

        [TestMethod]
        public void TestSubpatchSizeValidation()
        {
            var dems = new[] { @"/d/datasets/viper_v71_2024_medium\other\dem.tif" }
                        .Where(File.Exists)
                        .Select(path => new ElevationMap(path))
                        .ToList();

            if (dems.Count == 0)
            {
                Assert.Inconclusive("No test DEMs available");
                return;
            }

            using (var generator = new QuadTreeHorizonGenerator())
            {
                // Test valid subpatch sizes
                int[] validSizes = { 8, 16, 32, 64 };
                foreach (int size in validSizes)
                {
                    try
                    {
                        var result = generator.GenerateHorizonsWithSubpatches(dems, 100, 100, 128, 128, 0.0f, size);
                        Assert.IsNotNull(result);
                        Console.WriteLine($"Subpatch size {size}: ✅ Valid");
                    }
                    catch (Exception ex)
                    {
                        Assert.Fail($"Valid subpatch size {size} should not throw exception: {ex.Message}");
                    }
                }

                // Test invalid subpatch sizes
                int[] invalidSizes = { 1, 4, 7, 12, 20, 48, 100 };
                foreach (int size in invalidSizes)
                {
                    try
                    {
                        var result = generator.GenerateHorizonsWithSubpatches(dems, 100, 100, 128, 128, 0.0f, size);
                        Assert.Fail($"Invalid subpatch size {size} should throw ArgumentException");
                    }
                    catch (ArgumentException ex)
                    {
                        Console.WriteLine($"Subpatch size {size}: ✅ Correctly rejected - {ex.Message}");
                        // Expected
                    }
                    catch (Exception ex)
                    {
                        Assert.Fail($"Invalid subpatch size {size} should throw ArgumentException, not {ex.GetType().Name}: {ex.Message}");
                    }
                }
            }
        }
    }
}
