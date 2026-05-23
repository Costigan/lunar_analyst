#nullable enable

using moonlib.horizon;

namespace moonlib.tests
{
    /// <summary>
    /// Tests to analyze how polynomial approximation error scales with patch size
    /// </summary>
    [TestClass]
    public class PatchSizeScalingTests
    {
        private static readonly string[] DemPaths = new[] {
            @"/d/datasets/viper_v71_2024_medium/other/dem.tif",
            @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
            @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
        };

        [TestMethod]
        public void AnalyzePolynomialErrorVsPatchSize()
        {
            // Skip test if DEMs don't exist
            if (!DemPaths.All(File.Exists))
            {
                Assert.Inconclusive($"Required DEMs not found. Expected: {string.Join(", ", DemPaths)}");
                return;
            }

            // Test parameters - target pixel will be at upper-left corner (0,0) of each patch
            const int targetPixelX = 2048;
            const int targetPixelY = 128;
            const float observerElevation = 0.0f;
            const int targetAzimuthDegrees = 55;
            const int targetAzimuthIndex = (int)((targetAzimuthDegrees / 360.0) * 1440);

            var patchSizes = new[] { 1, 2, 4, 8, 16, 32, 64, 128 };

            Console.WriteLine($"Analyzing polynomial approximation error vs patch size");
            Console.WriteLine($"Target pixel: ({targetPixelX}, {targetPixelY}) - ALWAYS at upper-left corner (0,0) of each patch");
            Console.WriteLine($"Target azimuth: {targetAzimuthDegrees}° (bin {targetAzimuthIndex})");
            Console.WriteLine($"Patch sizes: {string.Join(", ", patchSizes)}");

            try
            {
                // Load DEMs
                var dems = DemPaths.Select(path => new ElevationMap(path)).ToList();
                Console.WriteLine($"\nLoaded {dems.Count} DEMs:");
                foreach (var dem in dems.Select((d, i) => new { dem = d, index = i }))
                {
                    Console.WriteLine($"  DEM {dem.index}: {dem.dem.Width}x{dem.dem.Height} pixels");
                }

                using (var generator = new QuadTreeHorizonGenerator())
                {
                    // Generate ground truth: 1x1 patch
                    Console.WriteLine($"\n=== Ground Truth (1x1 patch) ===");
                    var groundTruthHorizons = generator.GenerateHorizons(dems, targetPixelX, targetPixelY, 1, 1, observerElevation);
                    float groundTruthValue = groundTruthHorizons.Degrees[targetAzimuthIndex];
                    Console.WriteLine($"Ground truth horizon at {targetAzimuthDegrees}°: {groundTruthValue:F6}°");

                    // Extract full ground truth horizon for comprehensive analysis
                    var groundTruthFull = new float[1440];
                    for (int azIdx = 0; azIdx < 1440; azIdx++)
                    {
                        groundTruthFull[azIdx] = groundTruthHorizons.Degrees[azIdx];
                    }

                    Console.WriteLine($"\n=== Patch Size Analysis ===");
                    Console.WriteLine("Size\tTarget Az Error\tMax Error\tAvg Error\tBins >0.1°\tDistance from Center");

                    // Test each patch size (skip 1x1 since it's ground truth)
                    for (int i = 1; i < patchSizes.Length; i++)
                    {
                        int patchSize = patchSizes[i];
                        
                        // TARGET PIXEL IS ALWAYS AT UPPER-LEFT CORNER (0,0) OF EACH PATCH
                        // So patch origin is always at the target pixel location
                        int patchStartX = targetPixelX;
                        int patchStartY = targetPixelY;
                        
                        // Target pixel position within patch is always (0,0)
                        int pixelInPatchX = 0;
                        int pixelInPatchY = 0;
                        //int pixelIndex = 0;  // First pixel in the patch
                        
                        // Calculate distance from patch center where polynomial is fitted
                        float centerX = patchSize / 2.0f;
                        float centerY = patchSize / 2.0f;
                        float distanceFromCenter = (float)Math.Sqrt(
                            Math.Pow(pixelInPatchX - centerX, 2) + 
                            Math.Pow(pixelInPatchY - centerY, 2));

                        // Generate horizons for this patch size
                        var patchHorizons = generator.GenerateHorizons(dems, patchStartX, patchStartY, patchSize, patchSize, observerElevation);
                        
                        // Extract horizon for our target pixel (always at index 0)
                        var targetPixelHorizon = new float[1440];
                        for (int azIdx = 0; azIdx < 1440; azIdx++)
                        {
                            targetPixelHorizon[azIdx] = patchHorizons.Degrees[azIdx];  // First pixel's horizon
                        }

                        // Calculate errors
                        float targetAzError = Math.Abs(targetPixelHorizon[targetAzimuthIndex] - groundTruthValue);
                        
                        float maxError = 0;
                        float totalError = 0;
                        int significantErrorCount = 0;
                        
                        for (int azIdx = 0; azIdx < 1440; azIdx++)
                        {
                            float error = Math.Abs(targetPixelHorizon[azIdx] - groundTruthFull[azIdx]);
                            totalError += error;
                            
                            if (error > maxError)
                                maxError = error;
                                
                            if (error > 0.1f)
                                significantErrorCount++;
                        }
                        
                        float avgError = totalError / 1440f;
                        float percentSignificant = significantErrorCount * 100.0f / 1440f;

                        Console.WriteLine($"{patchSize}x{patchSize}\t{targetAzError:F6}°\t\t{maxError:F6}°\t{avgError:F6}°\t{significantErrorCount}/1440 ({percentSignificant:F1}%)\t{distanceFromCenter:F1} pixels");

                        // Detailed output for specific patch sizes
                        if (patchSize == 32 || patchSize == 64 || patchSize == 128)
                        {
                            Console.WriteLine($"  Patch {patchSize}x{patchSize}: start=({patchStartX},{patchStartY}), target pixel at patch coord (0,0)");
                            Console.WriteLine($"  Polynomial fitted at patch center ({centerX:F1},{centerY:F1}), distance = {distanceFromCenter:F1} pixels");
                            Console.WriteLine($"  Target pixel horizon: {targetPixelHorizon[targetAzimuthIndex]:F6}° vs ground truth {groundTruthValue:F6}°");
                        }
                    }

                    Console.WriteLine($"\n=== Analysis Summary ===");
                    Console.WriteLine("Key observations:");
                    Console.WriteLine("- Target pixel at upper-left corner gets farther from center as patch size increases");
                    Console.WriteLine("- 128x128: target pixel is ~90 pixels from center where polynomial was fitted");
                    Console.WriteLine("- This should show dramatic polynomial approximation breakdown");
                    Console.WriteLine("- Distance from center is key factor in errors");
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Test failed with exception: {ex.Message}");
                Console.WriteLine($"Stack trace: {ex.StackTrace}");
                throw;
            }
        }
    }
}
