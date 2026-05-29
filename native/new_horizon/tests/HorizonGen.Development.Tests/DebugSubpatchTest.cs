using Microsoft.VisualStudio.TestTools.UnitTesting;
using HorizonGen;
using System;
using System.Collections.Generic;
using System.IO;
using moonlib;
using moonlib.horizon;

namespace HorizonGen.Tests
{
    [TestClass]
    public class DebugSubpatchTest
    {
        [TestMethod]
        public void DebugSubpatchErrorAfterFix()
        {
            // Test pixel that showed 0.4° errors before
            int targetPixelX = 2048;
            int targetPixelY = 128; 
            int targetAzimuth = 55; // degrees
            int targetAzimuthIndex = (int)Math.Round(targetAzimuth / 0.25);
            float observerElevation = 0.0f;

            // Load DEMs
            string basePath = @"/c/viper_archive/dem_geotif_3";
            var demPaths = new List<string>
            {
                Path.Combine(basePath, "moc_r_07_08s_194e_256_1km.tif"),
                Path.Combine(basePath, "moc_r_07_08s_194e_256_200m.tif"),
                Path.Combine(basePath, "moc_r_07_08s_194e_256_40m.tif")
            };

            if (!File.Exists(demPaths[0]))
            {
                Console.WriteLine("DEM files not found, skipping test");
                return;
            }

            var dems = new List<ElevationMap>();
            foreach (string demPath in demPaths)
            {
                var dem = new ElevationMap(demPath);
                dems.Add(dem);
            }

            Console.WriteLine($"Testing pixel ({targetPixelX}, {targetPixelY}) at azimuth {targetAzimuth}°");
            Console.WriteLine($"Loaded {dems.Count} DEMs");

            var generator = new QuadTreeHorizonGenerator();

            // Get ground truth (1x1 patch)
            var groundTruth = generator.GenerateHorizonsWithSubpatches(dems, targetPixelX, targetPixelY, 1, 1, observerElevation, 1);
            float groundTruthValue = groundTruth.Degrees[targetAzimuthIndex];

            // Get standard 128x128 result  
            var standard128 = generator.GenerateHorizonsWithSubpatches(dems, targetPixelX, targetPixelY, 128, 128, observerElevation, 128);
            float standard128Value = standard128.Degrees[targetAzimuthIndex];
            float standard128Error = Math.Abs(standard128Value - groundTruthValue);

            var results = new System.Text.StringBuilder();
            results.AppendLine($"Ground truth (1x1): {groundTruthValue:F6}°");
            results.AppendLine($"Standard (128x128): {standard128Value:F6}° (error: {standard128Error:F6}°)");

            // Test subpatch sizes
            int[] sizes = { 64, 32, 16, 8 };
            foreach (int size in sizes)
            {
                var subpatchResult = generator.GenerateHorizonsWithSubpatches(dems, targetPixelX, targetPixelY, 128, 128, observerElevation, size);
                float subpatchValue = subpatchResult.Degrees[targetAzimuthIndex];
                float error = Math.Abs(subpatchValue - groundTruthValue);

                // Calculate max error across all azimuth bins
                float maxError = 0;
                for (int azIdx = 0; azIdx < 1440; azIdx++)
                {
                    float azError = Math.Abs(subpatchResult.Degrees[azIdx] - groundTruth.Degrees[azIdx]);
                    if (azError > maxError)
                        maxError = azError;
                }

                results.AppendLine($"Subpatch {size:D2}: target={subpatchValue:F6}° error={error:F6}° max={maxError:F6}°");
                
                // Force failure to show actual values
                if (error > 0.001f || maxError > 0.001f)
                {
                    Assert.Fail($"Large subpatch errors detected!\n{results}");
                }
            }

            // If we get here, errors are reasonable - just output results via assertion message
            Console.WriteLine(results.ToString());
            
            // Clean up - ElevationMap handles cleanup internally
        }
    }
}
