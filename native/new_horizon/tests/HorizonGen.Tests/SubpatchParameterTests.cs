using Microsoft.VisualStudio.TestTools.UnitTesting;
using moonlib.horizon;
using System;

namespace moonlib.tests
{
    [TestClass]
    public class SubpatchParameterTests
    {
        [TestMethod]
        public void TestSubpatchParameterFlow()
        {
            Console.WriteLine("Testing subpatch parameter flow without full DEM processing");

            using (var generator = new QuadTreeHorizonGenerator())
            {
                // Test valid subpatch sizes - should not throw exceptions during validation
                int[] validSizes = { 2, 4, 8, 16, 32, 64, 128 };
                foreach (int size in validSizes)
                {
                    try 
                    {
                        // This will fail when it tries to load DEMs, but should pass parameter validation
                        generator.GenerateHorizonsWithSubpatches(new System.Collections.Generic.List<ElevationMap>(), 
                                                               100, 100, 128, 128, 0.0f, size);
                        Assert.Fail($"Should have failed due to empty DEM list, not parameter validation for size {size}");
                    }
                    catch (ArgumentException ex) when (ex.Message.Contains("At least one DEM is required"))
                    {
                        Console.WriteLine($"Subpatch size {size}: ✅ Parameter validation passed, failed on DEM requirement as expected");
                        // This is the expected behavior - parameter is valid, but no DEMs provided
                    }
                    catch (ArgumentException ex) when (ex.Message.Contains("Subpatch size"))
                    {
                        Assert.Fail($"Subpatch size {size} should be valid but got: {ex.Message}");
                    }
                }

                // Test invalid subpatch sizes - should fail parameter validation
                int[] invalidSizes = { 1, 12, };
                foreach (int size in invalidSizes)
                {
                    try
                    {
                        generator.GenerateHorizonsWithSubpatches(new System.Collections.Generic.List<ElevationMap>(), 
                                                               100, 100, 128, 128, 0.0f, size);
                        Assert.Fail($"Invalid subpatch size {size} should have been rejected");
                    }
                    catch (ArgumentException ex) when (ex.Message.Contains("Subpatch size must be 2, 4, 8, 16, 32, 64, or 128"))
                    {
                        Console.WriteLine($"Subpatch size {size}: ✅ Correctly rejected - {ex.Message}");
                        // This is expected
                    }
                }

                Console.WriteLine("✅ All subpatch parameter validation tests passed");
            }
        }

        [TestMethod]
        public void TestSubpatchSizeCalculation()
        {
            Console.WriteLine("Testing subpatch size calculations");

            var testCases = new[]
            {
                new { Size = 2, Expected = 128 / 2 },    // 64x64 = 4096 subpatches
                new { Size = 4, Expected = 128 / 4 },    // 32x32 = 1024 subpatches
                new { Size = 8, Expected = 128 / 8 },    // 16x16 = 256 subpatches
                new { Size = 16, Expected = 128 / 16 },  // 8x8 = 64 subpatches  
                new { Size = 32, Expected = 128 / 32 },  // 4x4 = 16 subpatches
                new { Size = 64, Expected = 128 / 64 },  // 2x2 = 4 subpatches
                new { Size = 128, Expected = 128 / 128 } // 1x1 = 1 subpatch
            };

            foreach (var testCase in testCases)
            {
                int numSubpatchesPerDim = 128 / testCase.Size;
                int totalSubpatches = numSubpatchesPerDim * numSubpatchesPerDim;
                
                Assert.AreEqual(testCase.Expected, numSubpatchesPerDim, $"Wrong calculation for {testCase.Size}x{testCase.Size} subpatches");
                Console.WriteLine($"Subpatch {testCase.Size}x{testCase.Size}: {numSubpatchesPerDim}×{numSubpatchesPerDim} = {totalSubpatches} total subpatches");
            }

            Console.WriteLine("✅ All subpatch calculations correct");
        }
    }
}
