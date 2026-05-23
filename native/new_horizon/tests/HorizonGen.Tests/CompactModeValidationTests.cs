using moonlib.horizon;
using System.Diagnostics;

namespace moonlib.tests
{
    /// <summary>
    /// Tests to validate Compact Mode polynomial approximation accuracy
    /// by comparing large patch results vs single-pixel results.
    /// </summary>
    [TestClass]
    public class CompactModeValidationTests
    {
        private static readonly string[] DemPaths = new[] {
            @"/d/datasets/viper_v71_2024_medium/other/dem.tif",
            @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
            @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
        };

        [TestMethod]
        public void CompactMode_128x128_vs_1x1_at_TileCorner()
        {
            // Skip test if DEMs don't exist
            if (!DemPaths.All(File.Exists))
            {
                Assert.Inconclusive($"Required DEMs not found. Expected: {string.Join(", ", DemPaths)}");
                return;
            }

            // Test parameters
            const int testX = 2048;
            const int testY = 128;
            const float observerElevation = 0.0f;
            const int targetAzimuthDegrees = 55; // Focus azimuth as requested
            const int targetAzimuthIndex = (int)((targetAzimuthDegrees / 360.0) * 1440); // Convert to bin index

            Console.WriteLine($"Testing pixel ({testX}, {testY}) at azimuth {targetAzimuthDegrees}° (bin {targetAzimuthIndex})");

            try
            {
                // Load DEMs
                var dems = DemPaths.Select(path => new ElevationMap(path)).ToList();
                Console.WriteLine($"Loaded {dems.Count} DEMs:");
                foreach (var dem in dems.Select((d, i) => new { dem = d, index = i }))
                {
                    Console.WriteLine($"  DEM {dem.index}: {dem.dem.Width}x{dem.dem.Height} pixels");
                }

                using (var generator = new QuadTreeHorizonGenerator())
                {
                    // Test 1: Generate 128x128 patch starting at (testX, testY)
                    Console.WriteLine("\n=== 128x128 Patch Test ===");
                    var horizons128 = generator.GenerateHorizons(dems, testX, testY, 128, 128, observerElevation);
                    Console.WriteLine($"Generated 128x128 horizons: {horizons128.Degrees.Length} values");

                    // Extract horizon for top-left pixel (0, 0) in patch coordinates = (testX, testY) in DEM coordinates
                    var horizon128_pixel00 = new float[1440];
                    for (int azIdx = 0; azIdx < 1440; azIdx++)
                    {
                        int pixelIdx = 0; // Top-left pixel in the patch
                        horizon128_pixel00[azIdx] = horizons128.Degrees[pixelIdx * 1440 + azIdx];
                    }

                    // Test 2: Generate 1x1 patch at the exact same location
                    Console.WriteLine("\n=== 1x1 Patch Test ===");
                    var horizons1 = generator.GenerateHorizons(dems, testX, testY, 1, 1, observerElevation);
                    Console.WriteLine($"Generated 1x1 horizons: {horizons1.Degrees.Length} values");

                    // Extract horizon for the single pixel (0, 0) in patch coordinates = (testX, testY) in DEM coordinates
                    var horizon1_pixel00 = new float[1440];
                    for (int azIdx = 0; azIdx < 1440; azIdx++)
                    {
                        int pixelIdx = 0; // Only pixel in the 1x1 patch
                        horizon1_pixel00[azIdx] = horizons1.Degrees[pixelIdx * 1440 + azIdx];
                    }

                    // Compare horizons
                    Console.WriteLine("\n=== Comparison Results ===");
                    Console.WriteLine($"Target azimuth: {targetAzimuthDegrees}° (bin {targetAzimuthIndex})");
                    Console.WriteLine($"128x128 patch value: {horizon128_pixel00[targetAzimuthIndex]:F6}°");
                    Console.WriteLine($"1x1 patch value:    {horizon1_pixel00[targetAzimuthIndex]:F6}°");

                    float difference = horizon128_pixel00[targetAzimuthIndex] - horizon1_pixel00[targetAzimuthIndex];
                    Console.WriteLine($"Difference:         {difference:F6}° ({Math.Abs(difference):F6}° abs)");

                    // Statistical comparison across all azimuths
                    float maxDiff = 0;
                    int maxDiffIdx = -1;
                    float totalAbsDiff = 0;
                    int significantDiffCount = 0;

                    for (int azIdx = 0; azIdx < 1440; azIdx++)
                    {
                        float diff = Math.Abs(horizon128_pixel00[azIdx] - horizon1_pixel00[azIdx]);
                        totalAbsDiff += diff;

                        if (diff > maxDiff)
                        {
                            maxDiff = diff;
                            maxDiffIdx = azIdx;
                        }

                        if (diff > 0.1f) // More than 0.1 degree difference
                        {
                            significantDiffCount++;
                        }
                    }

                    float avgAbsDiff = totalAbsDiff / 1440f;
                    float maxDiffAzimuth = (maxDiffIdx / 1440f) * 360f;

                    Console.WriteLine($"\nStatistics across all 1440 azimuths:");
                    Console.WriteLine($"  Average absolute difference: {avgAbsDiff:F6}°");
                    Console.WriteLine($"  Maximum difference: {maxDiff:F6}° at azimuth {maxDiffAzimuth:F1}° (bin {maxDiffIdx})");
                    Console.WriteLine($"  Bins with >0.1° difference: {significantDiffCount}/1440 ({significantDiffCount * 100.0 / 1440:F1}%)");

                    // Test assertions
                    Console.WriteLine($"\n=== Test Results ===");

                    // Check target azimuth specifically
                    Assert.AreEqual(horizon1_pixel00[targetAzimuthIndex], horizon128_pixel00[targetAzimuthIndex], 0.01f,
                        $"Horizons should match within 0.01° at target azimuth {targetAzimuthDegrees}°. " +
                        $"128x128={horizon128_pixel00[targetAzimuthIndex]:F6}°, 1x1={horizon1_pixel00[targetAzimuthIndex]:F6}°");

                    // Check overall accuracy
                    Assert.IsTrue(avgAbsDiff < 0.05f,
                        $"Average difference should be <0.05°, but was {avgAbsDiff:F6}°. " +
                        $"This suggests Compact Mode polynomial approximation is breaking down.");

                    Assert.IsTrue(maxDiff < 0.25f,
                        $"Maximum difference should be <0.25° (one azimuth bin), but was {maxDiff:F6}° at azimuth {maxDiffAzimuth:F1}°. " +
                        $"This indicates significant polynomial approximation errors.");

                    Console.WriteLine("✅ Compact Mode polynomial approximation is accurate for this pixel location.");
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
        public void CompactMode_Performance_Test()
        {
            var dems = DemPaths.Select(path => new ElevationMap(path)).ToList();
            var generator = new QuadTreeHorizonGenerator();
            var sw = Stopwatch.StartNew();
            var horizons = generator.GenerateHorizons(dems, 0, 0, 128, 128, 0.0f);
            Console.WriteLine($"Old kernel time: {sw.Elapsed.TotalSeconds:F3}s");
        }
    }
}
