using Microsoft.VisualStudio.TestTools.UnitTesting;
using moonlib.horizon;

namespace moonlib.tests
{
    [TestClass]
    public class PipelineHorizonGeneratorDevelopmentTests
    {
        private const string InnerDemPath = @"/d/datasets/viper_v71_2024_medium/other/dem.tif";
        private const string MiddleDemPath = @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif";
        private const string OuterDemPath = @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif";

        public TestContext TestContext { get; set; }

        [TestMethod]
        [TestCategory("Development")]
        [TestCategory("ExternalData")]
        [TestCategory("Pipeline")]
        public async Task GenerateHorizonsForAllPatches_VIPER_DEMs()
        {
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

            var dems = new List<ElevationMap>
            {
                new ElevationMap(InnerDemPath),
                new ElevationMap(MiddleDemPath),
                new ElevationMap(OuterDemPath)
            };

            const int patchSize = 128;
            int numPatchesX = dems[0].Width / patchSize;
            int numPatchesY = dems[0].Height / patchSize;
            int totalPatches = numPatchesX * numPatchesY;
            int patchCount = totalPatches;

            Console.WriteLine($"Total patches available: {totalPatches} ({numPatchesX}x{numPatchesY})");
            Console.WriteLine($"Processing first N patches: {patchCount}");

            var outputDir = Path.Combine(TestContext.TestRunDirectory ?? Path.GetTempPath(), "PipelineTest_VIPER");
            if (Directory.Exists(outputDir))
            {
                Directory.Delete(outputDir, recursive: true);
            }
            Directory.CreateDirectory(outputDir);

            float observerElevation = 2.0f;
            var allPatches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
            var patchesToProcess = allPatches.Take(patchCount).ToList();

            Console.WriteLine($"Selected {patchesToProcess.Count} patches from {allPatches.Count} total");

            using var generator = new QuadTreeHorizonGenerator();
            var stopwatch = System.Diagnostics.Stopwatch.StartNew();

            await generator.GenerateHorizonsForPatches(outputDir, dems, patchesToProcess, observerElevation);

            stopwatch.Stop();

            var store = new HorizonTileStore(outputDir);
            var files = store.EnumerateFiles(observerElevationMeters: observerElevation).ToArray();
            Console.WriteLine($"Generated {files.Length} horizon files in {stopwatch.Elapsed.TotalSeconds:F2} seconds");
            Console.WriteLine($"Average time per patch: {stopwatch.Elapsed.TotalSeconds / patchCount:F2} seconds");

            Assert.AreEqual(patchCount, files.Length, "Should generate exactly N horizon files");

            string firstFileName = store.BuildFileName(0, 0, observerElevation, compress: false);
            string firstFilePath = store.BuildPath(0, 0, observerElevation, compress: false);
            Assert.IsTrue(File.Exists(firstFilePath), $"First horizon file should exist: {firstFileName}");

            var fileInfo = new FileInfo(firstFilePath);
            long expectedSize = patchSize * patchSize * 1440 * sizeof(float);
            Assert.AreEqual(expectedSize, fileInfo.Length, "Horizon file should have correct size");

            float[] horizons = Utilities.LoadBinaryArray<float>(firstFilePath);
            Assert.AreEqual(patchSize * patchSize * 1440, horizons.Length, "Horizon array should have correct length");
            Assert.IsTrue(horizons.All(float.IsFinite), "All horizon values should be finite");

            Console.WriteLine($"Test completed successfully. Output directory: {outputDir}");
        }
    }
}
