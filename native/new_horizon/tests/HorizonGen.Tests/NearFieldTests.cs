using moonlib.horizon;

#nullable disable

namespace moonlib.tests
{
    [TestClass]
    public class NearFieldTests
    {
        private const string DemPath = @"/d/datasets/viper_v71_2024_medium/other/dem.tif";
        private const float NearFieldClampMeters = 50f;
        private const double SlopeTolerance = 0.05; // ~2.8 degrees

        public class NearFieldTestCase
        {
            public int X { get; set; }
            public int Y { get; set; }
            public float Z { get; set; }
            public double Azimuth { get; set; }

            public NearFieldTestCase(int x, int y, float z, double az)
            {
                X = x; Y = y; Z = z; Azimuth = az;
            }
        }

        private static List<NearFieldTestCase> TestCases = new List<NearFieldTestCase>
        {
            // Initial case
            new NearFieldTestCase(837, 3280, 0.0f, 90.0),
        };

        [TestMethod]
        public void CompareNearFieldAndReferenceEmulators()
        {
            if (!File.Exists(DemPath))
            {
                Assert.Inconclusive($"DEM file not found at {DemPath}");
            }

            var outputDir = Path.Combine(TestContext.TestRunDirectory, "NearFieldTests");
            Directory.CreateDirectory(outputDir);

            var dem = new ElevationMap(DemPath);
            var dems = new List<ElevationMap> { dem };

            foreach (var testCase in TestCases)
            {
                RunTestCase(dems, testCase, outputDir);
            }
        }

        private void RunTestCase(List<ElevationMap> dems, NearFieldTestCase testCase, string outputDir)
        {
            var origin = new PixelOrigin { X = testCase.X, Y = testCase.Y, Z = testCase.Z };
            var baseName = $"trace_{testCase.X}_{testCase.Y}_{testCase.Azimuth:F1}";
            
            // 1. Reference Emulator (Clamped)
            var refTracePath = Path.Combine(outputDir, $"{baseName}_ref.csv");
            // Pass maxDistanceMeters = NearFieldClampMeters to match the near field range
            // Align start distance to 1.0m to match NearField (1px step)
            var refSlopes = ReferenceRayEmulator.Run(
                dems[0], 
                origin, 
                testCase.Azimuth, 
                refTracePath, 
                suppressCsv: false, 
                unifiedStepMode: false,
                maxDistanceMeters: NearFieldClampMeters,
                startDistanceMeters: 1.0
            ).Slopes;

            // 2. NearField Emulator
            var nfTracePath = Path.Combine(outputDir, $"{baseName}_nf.csv");
            var nfSlopes = NearFieldRayEmulator.Run(
                dems,
                origin,
                testCase.Azimuth,
                NearFieldClampMeters,
                testCase.Z,
                nfTracePath,
                suppressCsv: false
            );

            Assert.IsTrue(refSlopes.Any(), $"Reference emulator produced no slopes for case {baseName}");
            Assert.IsTrue(nfSlopes.Any(), $"NearField emulator produced no slopes for case {baseName}");

            // 3. Compare Max Slopes
            double maxRefSlope = refSlopes.Max();
            double maxNfSlope = nfSlopes.Max();
            double diff = Math.Abs(maxRefSlope - maxNfSlope);

            Console.WriteLine($"Case {baseName}: RefMax={maxRefSlope:F4}, NfMax={maxNfSlope:F4}, Diff={diff:F4}");

            if (diff > SlopeTolerance)
            {
                // If failed, we already have the traces generated.
                // Just log and fail.
                Assert.Fail($"Slope difference {diff:F4} exceeds tolerance {SlopeTolerance} for case {baseName}. (Ref: {maxRefSlope:F4}, NF: {maxNfSlope:F4})");
            }
        }

        public TestContext TestContext { get; set; }
    }
}
