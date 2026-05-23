using Microsoft.VisualStudio.TestTools.UnitTesting;
using moonlib.horizon;

namespace moonlib.tests
{
    [TestClass]
    public class PipelineHorizonGeneratorTests
    {
        // VIPER DEM paths for testing
        private const string InnerDemPath = @"/d/datasets/viper_v71_2024_medium/other/dem.tif";
        private const string MiddleDemPath = @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif";
        private const string OuterDemPath = @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif";
        
        public TestContext TestContext { get; set; }

        [TestMethod]
        [TestCategory("Integration")]
        [TestCategory("Pipeline")]
        public async Task GenerateHorizonsForAllPatches_VIPER_DEMs()
        {
            // Check if DEM files exist
            if (!File.Exists(InnerDemPath))
            {
                Assert.Inconclusive($"Inner DEM not found: {InnerDemPath}");
            }
            if (!File.Exists(MiddleDemPath))
            {
                Assert.Inconclusive($"Middle DEM not found: {MiddleDemPath}");
            }
            if (!File.Exists(OuterDemPath))
            {
                Assert.Inconclusive($"Outer DEM not found: {OuterDemPath}");
            }

            // Load DEMs
            var dems = new List<ElevationMap>
            {
                new ElevationMap(InnerDemPath),
                new ElevationMap(MiddleDemPath),
                new ElevationMap(OuterDemPath)
            };

            // Calculate number of patches
            // Inner DEM dimensions: 4992 x 5248
            const int PATCH_SIZE = 128;
            int numPatchesX = dems[0].Width / PATCH_SIZE;  // 4992 / 128 = 39
            int numPatchesY = dems[0].Height / PATCH_SIZE; // 5248 / 128 = 41
            int totalPatches = numPatchesX * numPatchesY;  // 39 * 41 = 1599
            
            // Calculate N - number of patches to process
            // Start with total patches; user will adjust later
            int N = totalPatches;
            
            Console.WriteLine($"Total patches available: {totalPatches} ({numPatchesX}x{numPatchesY})");
            Console.WriteLine($"Processing first N patches: {N}");

            // Setup output directory
            var outputDir = Path.Combine(TestContext.TestRunDirectory ?? Path.GetTempPath(), "PipelineTest_VIPER");
            if (Directory.Exists(outputDir))
            {
                Directory.Delete(outputDir, recursive: true);
            }
            Directory.CreateDirectory(outputDir);

            float observerElevation = 2.0f;

            // Generate full patch list and filter to first N patches using LINQ
            var allPatches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
            var patchesToProcess = allPatches.Take(N).ToList();
            
            Console.WriteLine($"Selected {patchesToProcess.Count} patches from {allPatches.Count} total");

            // Create generator and process patches
            using var generator = new QuadTreeHorizonGenerator();
            
            var stopwatch = System.Diagnostics.Stopwatch.StartNew();
            
            // Use the new pipeline method with filtered patch list
            await generator.GenerateHorizonsForPatches(outputDir, dems, patchesToProcess, observerElevation);
            
            stopwatch.Stop();

            // Verify results
            var files = Directory.GetFiles(outputDir, "horizon_*.bin");
            Console.WriteLine($"Generated {files.Length} horizon files in {stopwatch.Elapsed.TotalSeconds:F2} seconds");
            Console.WriteLine($"Average time per patch: {stopwatch.Elapsed.TotalSeconds / N:F2} seconds");
            
            Assert.AreEqual(N, files.Length, "Should generate exactly N horizon files");

            // Verify first file exists and has correct size
            string firstFileName = QuadTreeHorizonGenerator.BuildHorizonFilename(0, 0, observerElevation);
            string firstFilePath = Path.Combine(outputDir, firstFileName);
            Assert.IsTrue(File.Exists(firstFilePath), $"First horizon file should exist: {firstFileName}");

            // Verify file size
            var fileInfo = new FileInfo(firstFilePath);
            long expectedSize = PATCH_SIZE * PATCH_SIZE * 1440 * sizeof(float); // 94,371,840 bytes
            Assert.AreEqual(expectedSize, fileInfo.Length, "Horizon file should have correct size");

            // Load and verify first file contains valid data
            float[] horizons = Utilities.LoadBinaryArray<float>(firstFilePath);
            Assert.AreEqual(PATCH_SIZE * PATCH_SIZE * 1440, horizons.Length, "Horizon array should have correct length");
            
            // Verify all values are finite
            Assert.IsTrue(horizons.All(h => float.IsFinite(h)), "All horizon values should be finite");

            Console.WriteLine($"Test completed successfully. Output directory: {outputDir}");
        }

        [TestMethod]
        [TestCategory("Integration")]
        [TestCategory("Pipeline")]
        public void GenerateHorizonsForAllPatches_ValidatesDimensions()
        {
            // Create a DEM with invalid dimensions (not a multiple of 128)
            const int INVALID_SIZE = 1000; // Not divisible by 128
            float[,] elevation = new float[INVALID_SIZE, INVALID_SIZE];
            
            const string StereographicProj4 = @"+proj=stere +lat_0=90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +no_defs";
            double[] geoTransform = new double[]
            {
                -(INVALID_SIZE / 2.0) * 100.0,
                100.0,
                0,
                (INVALID_SIZE / 2.0) * 100.0,
                0,
                -100.0
            };
            
            var invalidDem = new ElevationMap(elevation, StereographicProj4, geoTransform);
            var dems = new List<ElevationMap> { invalidDem };
            
            var outputDir = Path.Combine(Path.GetTempPath(), "PipelineTest_Invalid");
            
            using var generator = new QuadTreeHorizonGenerator();
            
            // Should throw ArgumentException for invalid dimensions
            var exception = Assert.ThrowsException<ArgumentException>(() =>
            {
                generator.GenerateHorizonsForAllPatches(outputDir, dems, 2.0f);
            });
            
            Assert.IsTrue(exception.Message.Contains("multiple of 128"), 
                "Exception message should mention multiple of 128 requirement");
            
            Console.WriteLine($"Correctly rejected invalid DEM dimensions: {exception.Message}");
        }

        [TestMethod]
        [TestCategory("Fast")]
        [TestCategory("Pipeline")]
        public void GenerateHorizonsForAllPatches_ValidDimensions_SmallDEM()
        {
            // Create a small DEM with valid dimensions (multiple of 128)
            const int DEM_SIZE = 256; // 2x2 patches (256 / 128 = 2)
            float[,] elevation = new float[DEM_SIZE, DEM_SIZE];
            
            // Fill with some test data
            for (int r = 0; r < DEM_SIZE; r++)
            {
                for (int c = 0; c < DEM_SIZE; c++)
                {
                    elevation[r, c] = 0.0f;
                }
            }
            
            const string StereographicProj4 = @"+proj=stere +lat_0=90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +no_defs";
            double[] geoTransform = new double[]
            {
                -(DEM_SIZE / 2.0) * 100.0,
                100.0,
                0,
                (DEM_SIZE / 2.0) * 100.0,
                0,
                -100.0
            };
            
            var dem = new ElevationMap(elevation, StereographicProj4, geoTransform);
            var dems = new List<ElevationMap> { dem };
            
            var outputDir = Path.Combine(Path.GetTempPath(), "PipelineTest_Small");
            if (Directory.Exists(outputDir))
            {
                Directory.Delete(outputDir, recursive: true);
            }
            
            using var generator = new QuadTreeHorizonGenerator();
            generator.GenerateHorizonsForAllPatches(outputDir, dems, 2.0f);
            
            // Verify 4 files created (2x2 patches)
            var files = Directory.GetFiles(outputDir, "horizon_*.bin");
            Assert.AreEqual(4, files.Length, "Should generate 4 horizon files for 256x256 DEM");
            
            // Verify expected filenames exist
            var expectedFiles = new[]
            {
                "horizon_00000_00000_020.bin",
                "horizon_00128_00000_020.bin",
                "horizon_00000_00128_020.bin",
                "horizon_00128_00128_020.bin"
            };
            
            foreach (var expectedFile in expectedFiles)
            {
                var filePath = Path.Combine(outputDir, expectedFile);
                Assert.IsTrue(File.Exists(filePath), $"Expected file should exist: {expectedFile}");
            }
            
            Console.WriteLine($"Successfully generated {files.Length} horizon files for small DEM test");
        }
    }
}
