using moonlib;
using moonlib.horizon;
using moonlib.math;
using moonlib.pipeline;
using OSGeo.GDAL;

#nullable enable

namespace moonlib.tests
{
    [TestClass]
    public class DebugScenarioArtifactDiagnostics
    {
        private const string ScenarioDir = "/e/lunar_analyst_scenarios/debug_scenario";
        private static readonly string[] Case5DemPaths =
        {
            "/e/lunar_analyst_scenarios/debug_scenario/dem.tif",
            "/e/lunar_analyst_scenarios/haworth/dem.tif",
            "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
        };
        private const string TimestampLabel = "2027-10-29T22-00-00";
        private static readonly Vector3d SunVectorMeters = new(
            -148156503600.3822,
            -1902200999.7069519,
            -3950834811.5279264);
        private const int PatchSize = 128;
        private const int Width = 256;
        private const int Height = 256;
        private const int HorizonSamples = LightmapGenerator.HorizonSamples;
        private const float RadToDeg = 57.2957795f;

        public TestContext TestContext { get; set; } = null!;

        [TestMethod]
        public async Task DebugScenario_20271029T220000_RegenerateHorizonsAndWriteDiagnostics()
        {
            TestContext.WriteLine($"Checking debug scenario at {ScenarioDir}");
            if (!Directory.Exists(ScenarioDir))
                Assert.Inconclusive($"Debug scenario not found: {ScenarioDir}");

            string scenarioToml = Path.Combine(ScenarioDir, "scenario.toml");
            TestContext.WriteLine($"Checking scenario metadata at {scenarioToml}");
            if (!File.Exists(scenarioToml))
                Assert.Inconclusive($"Scenario metadata not found: {scenarioToml}");

            var demPaths = Case5DemPaths.ToList();
            TestContext.WriteLine($"Using horizon_runner case 5 DEM paths: {string.Join(", ", demPaths)}");
            var missing = demPaths.Where(path => !File.Exists(path)).ToArray();
            if (missing.Length > 0)
                Assert.Inconclusive($"One or more DEMs are missing: {string.Join(", ", missing)}");

            var testRunDirectory = Path.Combine(
                Directory.GetCurrentDirectory(),
                "TestResults",
                nameof(DebugScenarioArtifactDiagnostics));
            var outputRoot = Path.Combine(
                testRunDirectory,
                "debug_scenario_artifacts",
                "case5_20271029T220000");
            var horizonOutDir = Path.Combine(outputRoot, "horizons");
            var noHierarchyHorizonOutDir = Path.Combine(outputRoot, "horizons_no_hierarchy");
            var sunOutDir = Path.Combine(outputRoot, "sun_pipeline");
            var existingHorizonSunOutDir = Path.Combine(outputRoot, "sun_pipeline_existing_horizons");
            if (Directory.Exists(outputRoot))
                Directory.Delete(outputRoot, recursive: true);
            Directory.CreateDirectory(horizonOutDir);

            var dems = demPaths.Select(path => new ElevationMap(path)).ToList();
            var primaryDem = dems[0];
            Assert.AreEqual(Width, primaryDem.Width, "This diagnostic expects the debug scenario primary DEM width.");
            Assert.AreEqual(Height, primaryDem.Height, "This diagnostic expects the debug scenario primary DEM height.");

            var allHorizons = await GenerateCaseHorizonsAsync(
                horizonOutDir,
                dems,
                disableHierarchy: false);

            var pipeline = new LightmapPipeline();
            await pipeline.ExecuteAsync(
                new List<DateTime> { new(2027, 10, 29, 22, 0, 0, DateTimeKind.Utc) },
                sunOutDir,
                cameraOutDir: null,
                primaryDem,
                horizonOutDir,
                sunVectorProvider: _ => SunVectorMeters);

            var existingHorizonsDir = Path.Combine(ScenarioDir, "lighting", "horizons");
            if (Directory.Exists(existingHorizonsDir))
            {
                var existingPipeline = new LightmapPipeline();
                await existingPipeline.ExecuteAsync(
                    new List<DateTime> { new(2027, 10, 29, 22, 0, 0, DateTimeKind.Utc) },
                    existingHorizonSunOutDir,
                    cameraOutDir: null,
                    primaryDem,
                    existingHorizonsDir,
                    sunVectorProvider: _ => SunVectorMeters);
            }

            var sunFractionFloat = new float[Width * Height];
            var binaryCenter = new byte[Width * Height];
            var centerMarginDeg = new float[Width * Height];
            var sampledHorizonDeg = new float[Width * Height];
            var sunAzimuthDeg = new float[Width * Height];
            var sunElevationDeg = new float[Width * Height];
            var gridBinOffsetFloat = new float[Width * Height];
            var gridBinOffsetInt = new float[Width * Height];

            var convergenceByTile = EnumerateTiles().ToDictionary(
                tile => tile,
                tile => CalculateTileConvergence(primaryDem, tile.tileCol, tile.tileRow));

            for (int row = 0; row < Height; row++)
            {
                for (int col = 0; col < Width; col++)
                {
                    int pixelIdx = row * Width + col;
                    int horizonBase = pixelIdx * HorizonSamples;

                    var mat = primaryDem.GetMoonMEToENU(row, col);
                    var (azRad, elRad) = primaryDem.GetAzEl(SunVectorMeters, mat);
                    float azDeg = azRad * RadToDeg;
                    float elDeg = elRad * RadToDeg;
                    float horizonDeg = SampleHorizonElevationDeg(allHorizons, horizonBase, azDeg);
                    float marginDeg = elDeg - horizonDeg;
                    float fraction = LightmapGenerator.BuilderSunFraction(allHorizons, horizonBase, azDeg, elDeg);

                    sunAzimuthDeg[pixelIdx] = azDeg;
                    sunElevationDeg[pixelIdx] = elDeg;
                    sampledHorizonDeg[pixelIdx] = horizonDeg;
                    centerMarginDeg[pixelIdx] = marginDeg;
                    sunFractionFloat[pixelIdx] = fraction;
                    binaryCenter[pixelIdx] = marginDeg > 0f ? (byte)255 : (byte)0;

                    int tileCol = (col / PatchSize) * PatchSize;
                    int tileRow = (row / PatchSize) * PatchSize;
                    var gc = convergenceByTile[(tileCol, tileRow)];
                    float dCol = col - tileCol - (PatchSize / 2.0f);
                    float dRow = row - tileRow - (PatchSize / 2.0f);
                    float deltaGamma = gc.DGammaDx * dCol + gc.DGammaDy * dRow;
                    float binOffset = deltaGamma * (HorizonSamples / (2.0f * MathF.PI));
                    gridBinOffsetFloat[pixelIdx] = binOffset;
                    gridBinOffsetInt[pixelIdx] = MathF.Round(binOffset);
                }
            }

            WriteFloatTiff(Path.Combine(outputRoot, $"sun_fraction_float_{TimestampLabel}.tif"), sunFractionFloat, primaryDem);
            WriteByteTiff(Path.Combine(outputRoot, $"binary_center_shadow_{TimestampLabel}.tif"), binaryCenter, primaryDem);
            WriteFloatTiff(Path.Combine(outputRoot, $"sun_center_margin_deg_{TimestampLabel}.tif"), centerMarginDeg, primaryDem);
            WriteFloatTiff(Path.Combine(outputRoot, $"sampled_horizon_deg_{TimestampLabel}.tif"), sampledHorizonDeg, primaryDem);
            WriteFloatTiff(Path.Combine(outputRoot, $"sun_azimuth_deg_{TimestampLabel}.tif"), sunAzimuthDeg, primaryDem);
            WriteFloatTiff(Path.Combine(outputRoot, $"sun_elevation_deg_{TimestampLabel}.tif"), sunElevationDeg, primaryDem);
            WriteFloatTiff(Path.Combine(outputRoot, $"grid_bin_offset_float_{TimestampLabel}.tif"), gridBinOffsetFloat, primaryDem);
            WriteFloatTiff(Path.Combine(outputRoot, $"grid_bin_offset_int_{TimestampLabel}.tif"), gridBinOffsetInt, primaryDem);

            WriteHorizontalNeighborDeltaDiagnostics(
                outputRoot,
                primaryDem,
                sampledHorizonDeg,
                sunFractionFloat);
            WriteBottomPatchBoundaryDiagnostics(
                outputRoot,
                sunAzimuthDeg,
                sunElevationDeg,
                sampledHorizonDeg,
                centerMarginDeg,
                sunFractionFloat);

            WriteHorizonPairDiagnostics(
                outputRoot,
                primaryDem,
                allHorizons,
                new[]
                {
                    new PixelPair("bottom_patch_boundary_y160", 127, 160, 128, 160),
                    new PixelPair("bottom_patch_boundary_y176", 127, 176, 128, 176),
                    new PixelPair("bottom_patch_boundary_y192", 127, 192, 128, 192),
                    new PixelPair("bottom_patch_boundary_y208", 127, 208, 128, 208),
                    new PixelPair("bottom_patch_boundary_y224", 127, 224, 128, 224),
                    new PixelPair("bottom_patch_control_y192_left", 95, 192, 96, 192),
                    new PixelPair("bottom_patch_control_y192_right", 159, 192, 160, 192),
                });
            WriteReferenceComparisonDiagnostics(
                outputRoot,
                dems,
                allHorizons,
                new[]
                {
                    new PixelPair("worst_bottom_patch_boundary_y199", 127, 199, 128, 199),
                    new PixelPair("bottom_patch_boundary_y192", 127, 192, 128, 192),
                    new PixelPair("bottom_patch_control_y192_left", 95, 192, 96, 192),
                    new PixelPair("bottom_patch_control_y192_right", 159, 192, 160, 192),
                });
            var noHierarchyHorizons = await GenerateCaseHorizonsAsync(
                noHierarchyHorizonOutDir,
                dems,
                disableHierarchy: true);
            WriteAlgorithmVariantDiagnostics(
                outputRoot,
                "no_hierarchy",
                primaryDem,
                noHierarchyHorizons);
            WriteSinglePixelPatchDiagnostics(
                outputRoot,
                dems,
                allHorizons,
                new PixelPair("worst_bottom_patch_boundary_y199", 127, 199, 128, 199));

            TestContext.WriteLine($"Wrote debug scenario diagnostics to {outputRoot}");
        }

        private static List<string> ReadDemPaths(string scenarioToml, string scenarioDir)
        {
            var result = new List<string>();
            string[] lines = File.ReadAllLines(scenarioToml);

            for (int i = 0; i < lines.Length; i++)
            {
                string trimmed = lines[i].Trim();
                if (trimmed.StartsWith("primary_path", StringComparison.Ordinal))
                {
                    string primary = ExtractQuotedValue(trimmed);
                    result.Add(Path.GetFullPath(Path.Combine(scenarioDir, primary)));
                }
                else if (trimmed.StartsWith("surrounding_paths", StringComparison.Ordinal))
                {
                    for (i++; i < lines.Length; i++)
                    {
                        string pathLine = lines[i].Trim();
                        if (pathLine.StartsWith("]", StringComparison.Ordinal))
                            break;
                        string value = ExtractQuotedValue(pathLine);
                        if (!string.IsNullOrWhiteSpace(value))
                            result.Add(value);
                        if (pathLine.Contains(']'))
                            break;
                    }
                }
            }

            if (result.Count == 0)
                throw new InvalidDataException($"No DEM paths found in {scenarioToml}");
            return result;
        }

        private static string ExtractQuotedValue(string line)
        {
            int first = line.IndexOf('"');
            if (first < 0)
                return string.Empty;
            int second = line.IndexOf('"', first + 1);
            return second < 0 ? string.Empty : line.Substring(first + 1, second - first - 1);
        }

        private static IEnumerable<(int tileCol, int tileRow)> EnumerateTiles()
        {
            for (int tileRow = 0; tileRow < Height; tileRow += PatchSize)
                for (int tileCol = 0; tileCol < Width; tileCol += PatchSize)
                    yield return (tileCol, tileRow);
        }

        private static void CopyTileHorizons(float[] tileHorizons, float[] allHorizons, int tileCol, int tileRow)
        {
            for (int localRow = 0; localRow < PatchSize; localRow++)
            {
                for (int localCol = 0; localCol < PatchSize; localCol++)
                {
                    int srcPixel = localRow * PatchSize + localCol;
                    int dstPixel = (tileRow + localRow) * Width + (tileCol + localCol);
                    Array.Copy(
                        tileHorizons,
                        srcPixel * HorizonSamples,
                        allHorizons,
                        dstPixel * HorizonSamples,
                        HorizonSamples);
                }
            }
        }

        private static async Task<float[]> GenerateCaseHorizonsAsync(
            string horizonOutDir,
            List<ElevationMap> dems,
            bool disableHierarchy)
        {
            Directory.CreateDirectory(horizonOutDir);
            var allHorizons = new float[Width * Height * HorizonSamples];
            using var generator = new QuadTreeHorizonGenerator(
                disableHierarchy: disableHierarchy,
                enableNearFieldReferenceMerge: false,
                nearFieldClampMeters: 250f);

            var patches = EnumerateTiles()
                .Select((tile, index) => new QuadTreeHorizonGenerator.PatchDescriptor
                {
                    TileX = tile.tileCol,
                    TileY = tile.tileRow,
                    Index = index,
                    PatchX = tile.tileCol / PatchSize,
                    PatchY = tile.tileRow / PatchSize
                })
                .ToList();

            await generator.GenerateHorizonsForPatches(
                horizonOutDir,
                dems,
                patches,
                observerElevation: 0f,
                compressHorizons: true);

            foreach (var (tileCol, tileRow) in EnumerateTiles())
            {
                string cbinName = Path.ChangeExtension(
                    QuadTreeHorizonGenerator.BuildHorizonFilename(tileCol, tileRow, observerElevation: 0f),
                    ".cbin");
                string cbinPath = Path.Combine(horizonOutDir, cbinName);
                var horizons = HorizonFile.ReadHorizonFile(cbinPath);
                CopyTileHorizons(horizons, allHorizons, tileCol, tileRow);
            }

            return allHorizons;
        }

        private readonly record struct TileConvergence(float GammaCenter, float DGammaDx, float DGammaDy);

        private readonly record struct PixelPair(string Label, int X0, int Y0, int X1, int Y1);

        private readonly record struct PixelSunSummary(
            float SunAzimuthDeg,
            float SunElevationDeg,
            float SampledHorizonDeg,
            float SunCenterMarginDeg,
            float SunFraction);

        private static void WriteHorizonPairDiagnostics(
            string outputRoot,
            ElevationMap dem,
            float[] allHorizons,
            IReadOnlyList<PixelPair> pairs)
        {
            string pairDir = Path.Combine(outputRoot, "horizon_pairs");
            Directory.CreateDirectory(pairDir);

            foreach (var pair in pairs)
            {
                int p0 = pair.Y0 * Width + pair.X0;
                int p1 = pair.Y1 * Width + pair.X1;
                int base0 = p0 * HorizonSamples;
                int base1 = p1 * HorizonSamples;

                var s0 = CalculatePixelSunSummary(dem, allHorizons, pair.X0, pair.Y0);
                var s1 = CalculatePixelSunSummary(dem, allHorizons, pair.X1, pair.Y1);

                float maxAbsDelta = 0f;
                int maxAbsDeltaIndex = 0;

                string csvPath = Path.Combine(pairDir, $"horizon_pair_{pair.Label}.csv");
                using (var writer = new StreamWriter(csvPath))
                {
                    writer.WriteLine("azimuth_deg,horizon_a_deg,horizon_b_deg,delta_b_minus_a_deg,is_near_sun_a,is_near_sun_b");
                    for (int azIdx = 0; azIdx < HorizonSamples; azIdx++)
                    {
                        float azDeg = azIdx * 360f / HorizonSamples;
                        float h0 = allHorizons[base0 + azIdx];
                        float h1 = allHorizons[base1 + azIdx];
                        float delta = h1 - h0;
                        float absDelta = MathF.Abs(delta);
                        if (absDelta > maxAbsDelta)
                        {
                            maxAbsDelta = absDelta;
                            maxAbsDeltaIndex = azIdx;
                        }

                        bool nearSunA = CircularAbsDeltaDeg(azDeg, s0.SunAzimuthDeg) <= 1.0f;
                        bool nearSunB = CircularAbsDeltaDeg(azDeg, s1.SunAzimuthDeg) <= 1.0f;
                        writer.WriteLine(
                            $"{azDeg:F6},{h0:F9},{h1:F9},{delta:F9},{nearSunA},{nearSunB}");
                    }
                }

                string summaryPath = Path.Combine(pairDir, $"horizon_pair_{pair.Label}_summary.txt");
                using (var writer = new StreamWriter(summaryPath))
                {
                    writer.WriteLine($"label={pair.Label}");
                    writer.WriteLine($"pixel_a=({pair.X0},{pair.Y0})");
                    writer.WriteLine($"pixel_b=({pair.X1},{pair.Y1})");
                    writer.WriteLine($"patch_a=({pair.X0 / PatchSize},{pair.Y0 / PatchSize})");
                    writer.WriteLine($"patch_b=({pair.X1 / PatchSize},{pair.Y1 / PatchSize})");
                    writer.WriteLine();
                    WriteSummary(writer, "a", s0);
                    WriteSummary(writer, "b", s1);
                    writer.WriteLine();
                    writer.WriteLine($"delta_sampled_horizon_b_minus_a_deg={s1.SampledHorizonDeg - s0.SampledHorizonDeg:F9}");
                    writer.WriteLine($"delta_margin_b_minus_a_deg={s1.SunCenterMarginDeg - s0.SunCenterMarginDeg:F9}");
                    writer.WriteLine($"delta_sun_fraction_b_minus_a={s1.SunFraction - s0.SunFraction:F9}");
                    writer.WriteLine($"max_abs_horizon_delta_deg={maxAbsDelta:F9}");
                    writer.WriteLine($"max_abs_horizon_delta_azimuth_deg={maxAbsDeltaIndex * 360f / HorizonSamples:F6}");
                    writer.WriteLine($"csv={csvPath}");
                }
            }
        }

        private static PixelSunSummary CalculatePixelSunSummary(ElevationMap dem, float[] allHorizons, int x, int y)
        {
            int pixelIdx = y * Width + x;
            int horizonBase = pixelIdx * HorizonSamples;
            var mat = dem.GetMoonMEToENU(y, x);
            var (azRad, elRad) = dem.GetAzEl(SunVectorMeters, mat);
            float azDeg = azRad * RadToDeg;
            float elDeg = elRad * RadToDeg;
            float horizonDeg = SampleHorizonElevationDeg(allHorizons, horizonBase, azDeg);
            float fraction = LightmapGenerator.BuilderSunFraction(allHorizons, horizonBase, azDeg, elDeg);
            return new PixelSunSummary(
                azDeg,
                elDeg,
                horizonDeg,
                elDeg - horizonDeg,
                fraction);
        }

        private static void WriteSummary(StreamWriter writer, string prefix, PixelSunSummary summary)
        {
            writer.WriteLine($"{prefix}.sun_azimuth_deg={summary.SunAzimuthDeg:F9}");
            writer.WriteLine($"{prefix}.sun_elevation_deg={summary.SunElevationDeg:F9}");
            writer.WriteLine($"{prefix}.sampled_horizon_deg={summary.SampledHorizonDeg:F9}");
            writer.WriteLine($"{prefix}.sun_center_margin_deg={summary.SunCenterMarginDeg:F9}");
            writer.WriteLine($"{prefix}.sun_fraction={summary.SunFraction:F9}");
        }

        private static void WriteHorizontalNeighborDeltaDiagnostics(
            string outputRoot,
            ElevationMap dem,
            float[] sampledHorizonDeg,
            float[] sunFraction)
        {
            var horizonDelta = new float[Width * Height];
            var sunFractionDelta = new float[Width * Height];
            var sunFractionDeltaByte = new byte[Width * Height];
            Array.Fill(horizonDelta, -9999f);
            Array.Fill(sunFractionDelta, -9999f);
            Array.Fill(sunFractionDeltaByte, (byte)127);

            for (int row = 0; row < Height; row++)
            {
                for (int col = 1; col < Width; col++)
                {
                    int idx = row * Width + col;
                    int left = idx - 1;
                    horizonDelta[idx] = sampledHorizonDeg[idx] - sampledHorizonDeg[left];
                    sunFractionDelta[idx] = sunFraction[idx] - sunFraction[left];
                    sunFractionDeltaByte[idx] = ScaleSignedUnitDeltaToByte(sunFractionDelta[idx], maxAbsValue: 0.5f);
                }
            }

            WriteFloatTiff(
                Path.Combine(outputRoot, $"horizontal_neighbor_sampled_horizon_delta_deg_{TimestampLabel}.tif"),
                horizonDelta,
                dem);
            WriteFloatTiff(
                Path.Combine(outputRoot, $"horizontal_neighbor_sun_fraction_delta_{TimestampLabel}.tif"),
                sunFractionDelta,
                dem);
            WriteByteTiff(
                Path.Combine(outputRoot, $"horizontal_neighbor_sun_fraction_delta_byte_m05_p05_{TimestampLabel}.tif"),
                sunFractionDeltaByte,
                dem);
        }

        private static byte ScaleSignedUnitDeltaToByte(float value, float maxAbsValue)
        {
            float clamped = Math.Clamp(value, -maxAbsValue, maxAbsValue);
            float normalized = (clamped + maxAbsValue) / (2.0f * maxAbsValue);
            return (byte)Math.Clamp(MathF.Round(normalized * 255.0f), 0.0f, 255.0f);
        }

        private static void WriteBottomPatchBoundaryDiagnostics(
            string outputRoot,
            float[] sunAzimuthDeg,
            float[] sunElevationDeg,
            float[] sampledHorizonDeg,
            float[] centerMarginDeg,
            float[] sunFraction)
        {
            string path = Path.Combine(outputRoot, $"bottom_patch_boundary_x127_x128_{TimestampLabel}.csv");
            using var writer = new StreamWriter(path);
            writer.WriteLine(
                "y,a_x,b_x,a_sun_azimuth_deg,b_sun_azimuth_deg,a_sun_elevation_deg,b_sun_elevation_deg," +
                "a_sampled_horizon_deg,b_sampled_horizon_deg,delta_sampled_horizon_b_minus_a_deg," +
                "a_margin_deg,b_margin_deg,delta_margin_b_minus_a_deg," +
                "a_sun_fraction,b_sun_fraction,delta_sun_fraction_b_minus_a");

            for (int row = PatchSize; row < Height; row++)
            {
                const int aCol = PatchSize - 1;
                const int bCol = PatchSize;
                int a = row * Width + aCol;
                int b = row * Width + bCol;

                writer.WriteLine(
                    $"{row},{aCol},{bCol}," +
                    $"{sunAzimuthDeg[a]:F9},{sunAzimuthDeg[b]:F9}," +
                    $"{sunElevationDeg[a]:F9},{sunElevationDeg[b]:F9}," +
                    $"{sampledHorizonDeg[a]:F9},{sampledHorizonDeg[b]:F9},{sampledHorizonDeg[b] - sampledHorizonDeg[a]:F9}," +
                    $"{centerMarginDeg[a]:F9},{centerMarginDeg[b]:F9},{centerMarginDeg[b] - centerMarginDeg[a]:F9}," +
                    $"{sunFraction[a]:F9},{sunFraction[b]:F9},{sunFraction[b] - sunFraction[a]:F9}");
            }
        }

        private static void WriteReferenceComparisonDiagnostics(
            string outputRoot,
            List<ElevationMap> dems,
            float[] allHorizons,
            IReadOnlyList<PixelPair> pairs)
        {
            string referenceDir = Path.Combine(outputRoot, "reference_comparison");
            Directory.CreateDirectory(referenceDir);

            var refGen = new ReferenceHorizonGenerator();
            var pixels = pairs
                .SelectMany(pair => new[] { (pair.X0, pair.Y0), (pair.X1, pair.Y1) })
                .Distinct()
                .ToArray();
            var referenceHorizons = new Dictionary<(int X, int Y), float[]>();

            foreach (var (x, y) in pixels)
            {
                var origin = new PixelOrigin { X = x, Y = y, Z = 0f };
                referenceHorizons[(x, y)] = refGen.GenerateFromPixel(origin, dems).Elevations;
            }

            foreach (var (x, y) in pixels)
            {
                WriteReferencePixelComparison(referenceDir, allHorizons, referenceHorizons[(x, y)], x, y);
            }

            string summaryPath = Path.Combine(referenceDir, "reference_pair_summary.csv");
            using var writer = new StreamWriter(summaryPath);
            writer.WriteLine(
                "label,a_x,a_y,b_x,b_y,sun_azimuth_deg," +
                "qt_a_sampled_horizon_deg,qt_b_sampled_horizon_deg,qt_delta_b_minus_a_deg," +
                "ref_a_sampled_horizon_deg,ref_b_sampled_horizon_deg,ref_delta_b_minus_a_deg," +
                "qt_minus_ref_a_deg,qt_minus_ref_b_deg");

            foreach (var pair in pairs)
            {
                var sunA = CalculatePixelSunSummary(dems[0], allHorizons, pair.X0, pair.Y0);
                var sunB = CalculatePixelSunSummary(dems[0], allHorizons, pair.X1, pair.Y1);
                float sunAzimuthDeg = 0.5f * (sunA.SunAzimuthDeg + sunB.SunAzimuthDeg);

                float qtA = SamplePixelHorizonElevationDeg(allHorizons, pair.X0, pair.Y0, sunAzimuthDeg);
                float qtB = SamplePixelHorizonElevationDeg(allHorizons, pair.X1, pair.Y1, sunAzimuthDeg);
                float refA = SampleHorizonElevationDeg(referenceHorizons[(pair.X0, pair.Y0)], sunAzimuthDeg);
                float refB = SampleHorizonElevationDeg(referenceHorizons[(pair.X1, pair.Y1)], sunAzimuthDeg);

                writer.WriteLine(
                    $"{pair.Label},{pair.X0},{pair.Y0},{pair.X1},{pair.Y1},{sunAzimuthDeg:F9}," +
                    $"{qtA:F9},{qtB:F9},{qtB - qtA:F9}," +
                    $"{refA:F9},{refB:F9},{refB - refA:F9}," +
                    $"{qtA - refA:F9},{qtB - refB:F9}");
            }
        }

        private static void WriteReferencePixelComparison(
            string referenceDir,
            float[] allHorizons,
            float[] referenceHorizonDeg,
            int x,
            int y)
        {
            int pixelIdx = y * Width + x;
            int horizonBase = pixelIdx * HorizonSamples;
            float maxAbsDiff = 0f;
            int maxAbsDiffIndex = 0;

            string csvPath = Path.Combine(referenceDir, $"reference_vs_quadtree_x{x}_y{y}.csv");
            using (var writer = new StreamWriter(csvPath))
            {
                writer.WriteLine("index,azimuth_deg,reference_horizon_deg,quadtree_horizon_deg,quadtree_minus_reference_deg");
                for (int i = 0; i < HorizonSamples; i++)
                {
                    float referenceDeg = referenceHorizonDeg[ReferenceHorizonGenerator.ConvertHorizonIndexToQuadTreeIndex(i)];
                    float quadtreeDeg = allHorizons[horizonBase + i];
                    float diff = quadtreeDeg - referenceDeg;
                    float absDiff = MathF.Abs(diff);
                    if (absDiff > maxAbsDiff)
                    {
                        maxAbsDiff = absDiff;
                        maxAbsDiffIndex = i;
                    }

                    writer.WriteLine($"{i},{i * 360f / HorizonSamples:F6},{referenceDeg:F9},{quadtreeDeg:F9},{diff:F9}");
                }
            }

            string summaryPath = Path.Combine(referenceDir, $"reference_vs_quadtree_x{x}_y{y}_summary.txt");
            using var summary = new StreamWriter(summaryPath);
            summary.WriteLine($"pixel=({x},{y})");
            summary.WriteLine($"patch=({x / PatchSize},{y / PatchSize})");
            summary.WriteLine($"max_abs_quadtree_minus_reference_deg={maxAbsDiff:F9}");
            summary.WriteLine($"max_abs_quadtree_minus_reference_azimuth_deg={maxAbsDiffIndex * 360f / HorizonSamples:F6}");
            summary.WriteLine($"csv={csvPath}");
        }

        private static void WriteAlgorithmVariantDiagnostics(
            string outputRoot,
            string variantName,
            ElevationMap dem,
            float[] allHorizons)
        {
            string variantRoot = Path.Combine(outputRoot, variantName);
            Directory.CreateDirectory(variantRoot);

            var sunFractionFloat = new float[Width * Height];
            var sampledHorizonDeg = new float[Width * Height];
            var centerMarginDeg = new float[Width * Height];
            var sunAzimuthDeg = new float[Width * Height];
            var sunElevationDeg = new float[Width * Height];

            for (int row = 0; row < Height; row++)
            {
                for (int col = 0; col < Width; col++)
                {
                    int pixelIdx = row * Width + col;
                    int horizonBase = pixelIdx * HorizonSamples;
                    var mat = dem.GetMoonMEToENU(row, col);
                    var (azRad, elRad) = dem.GetAzEl(SunVectorMeters, mat);
                    float azDeg = azRad * RadToDeg;
                    float elDeg = elRad * RadToDeg;
                    float horizonDeg = SampleHorizonElevationDeg(allHorizons, horizonBase, azDeg);

                    sunAzimuthDeg[pixelIdx] = azDeg;
                    sunElevationDeg[pixelIdx] = elDeg;
                    sampledHorizonDeg[pixelIdx] = horizonDeg;
                    centerMarginDeg[pixelIdx] = elDeg - horizonDeg;
                    sunFractionFloat[pixelIdx] = LightmapGenerator.BuilderSunFraction(allHorizons, horizonBase, azDeg, elDeg);
                }
            }

            WriteFloatTiff(
                Path.Combine(variantRoot, $"sampled_horizon_deg_{TimestampLabel}.tif"),
                sampledHorizonDeg,
                dem);
            WriteFloatTiff(
                Path.Combine(variantRoot, $"sun_fraction_float_{TimestampLabel}.tif"),
                sunFractionFloat,
                dem);
            WriteHorizontalNeighborDeltaDiagnostics(
                variantRoot,
                dem,
                sampledHorizonDeg,
                sunFractionFloat);
            WriteBottomPatchBoundaryDiagnostics(
                variantRoot,
                sunAzimuthDeg,
                sunElevationDeg,
                sampledHorizonDeg,
                centerMarginDeg,
                sunFractionFloat);
        }

        private static void WriteSinglePixelPatchDiagnostics(
            string outputRoot,
            List<ElevationMap> dems,
            float[] fullPatchHorizons,
            PixelPair pair)
        {
            string singlePixelDir = Path.Combine(outputRoot, "single_pixel_patch_comparison");
            Directory.CreateDirectory(singlePixelDir);

            using var generator = new QuadTreeHorizonGenerator(
                disableHierarchy: false,
                enableNearFieldReferenceMerge: false,
                nearFieldClampMeters: 250f);
            var aHorizon = generator.GenerateHorizons(dems, pair.X0, pair.Y0, 1, 1, observerElevation: 0f).Degrees;
            var bHorizon = generator.GenerateHorizons(dems, pair.X1, pair.Y1, 1, 1, observerElevation: 0f).Degrees;
            var aFullPatchSummary = CalculatePixelSunSummary(dems[0], fullPatchHorizons, pair.X0, pair.Y0);
            var bFullPatchSummary = CalculatePixelSunSummary(dems[0], fullPatchHorizons, pair.X1, pair.Y1);
            float sunAzimuthDeg = 0.5f * (aFullPatchSummary.SunAzimuthDeg + bFullPatchSummary.SunAzimuthDeg);
            float sunElevationDeg = 0.5f * (aFullPatchSummary.SunElevationDeg + bFullPatchSummary.SunElevationDeg);

            float fullPatchA = SamplePixelHorizonElevationDeg(fullPatchHorizons, pair.X0, pair.Y0, sunAzimuthDeg);
            float fullPatchB = SamplePixelHorizonElevationDeg(fullPatchHorizons, pair.X1, pair.Y1, sunAzimuthDeg);
            float singlePixelA = SampleHorizonElevationDeg(aHorizon, sunAzimuthDeg);
            float singlePixelB = SampleHorizonElevationDeg(bHorizon, sunAzimuthDeg);
            float singlePixelFractionA = LightmapGenerator.BuilderSunFraction(aHorizon, 0, sunAzimuthDeg, sunElevationDeg);
            float singlePixelFractionB = LightmapGenerator.BuilderSunFraction(bHorizon, 0, sunAzimuthDeg, sunElevationDeg);

            string summaryPath = Path.Combine(singlePixelDir, $"{pair.Label}_summary.txt");
            using (var writer = new StreamWriter(summaryPath))
            {
                writer.WriteLine($"label={pair.Label}");
                writer.WriteLine($"pixel_a=({pair.X0},{pair.Y0})");
                writer.WriteLine($"pixel_b=({pair.X1},{pair.Y1})");
                writer.WriteLine($"sun_azimuth_deg={sunAzimuthDeg:F9}");
                writer.WriteLine($"sun_elevation_deg={sunElevationDeg:F9}");
                writer.WriteLine($"full_patch_a_sampled_horizon_deg={fullPatchA:F9}");
                writer.WriteLine($"full_patch_b_sampled_horizon_deg={fullPatchB:F9}");
                writer.WriteLine($"full_patch_delta_b_minus_a_deg={fullPatchB - fullPatchA:F9}");
                writer.WriteLine($"single_pixel_a_sampled_horizon_deg={singlePixelA:F9}");
                writer.WriteLine($"single_pixel_b_sampled_horizon_deg={singlePixelB:F9}");
                writer.WriteLine($"single_pixel_delta_b_minus_a_deg={singlePixelB - singlePixelA:F9}");
                writer.WriteLine($"single_pixel_a_sun_fraction={singlePixelFractionA:F9}");
                writer.WriteLine($"single_pixel_b_sun_fraction={singlePixelFractionB:F9}");
                writer.WriteLine($"single_pixel_delta_sun_fraction_b_minus_a={singlePixelFractionB - singlePixelFractionA:F9}");
            }

            string csvPath = Path.Combine(singlePixelDir, $"{pair.Label}.csv");
            using var csv = new StreamWriter(csvPath);
            csv.WriteLine("index,azimuth_deg,full_patch_a_deg,full_patch_b_deg,single_pixel_a_deg,single_pixel_b_deg,full_patch_delta_b_minus_a_deg,single_pixel_delta_b_minus_a_deg");
            int fullAIdx = (pair.Y0 * Width + pair.X0) * HorizonSamples;
            int fullBIdx = (pair.Y1 * Width + pair.X1) * HorizonSamples;
            for (int i = 0; i < HorizonSamples; i++)
            {
                float fullA = fullPatchHorizons[fullAIdx + i];
                float fullB = fullPatchHorizons[fullBIdx + i];
                csv.WriteLine(
                    $"{i},{i * 360f / HorizonSamples:F6}," +
                    $"{fullA:F9},{fullB:F9},{aHorizon[i]:F9},{bHorizon[i]:F9}," +
                    $"{fullB - fullA:F9},{bHorizon[i] - aHorizon[i]:F9}");
            }
        }

        private static float CircularAbsDeltaDeg(float a, float b)
        {
            float delta = MathF.Abs((a - b) % 360f);
            return delta > 180f ? 360f - delta : delta;
        }

        private static TileConvergence CalculateTileConvergence(ElevationMap dem, int tileCol, int tileRow)
        {
            var srs = dem.SrsDescriptor;
            if (srs is null || srs.Type != SrsDescriptor.ProjType.Stereographic)
                return new TileConvergence(0f, 0f, 0f);

            double centerCol = tileCol + PatchSize / 2.0;
            double centerRow = tileRow + PatchSize / 2.0;

            double gammaCenter = GetGamma(dem, centerCol, centerRow);
            double gammaRight = GetGamma(dem, centerCol + 1.0, centerRow);
            double gammaDown = GetGamma(dem, centerCol, centerRow + 1.0);

            return new TileConvergence(
                (float)gammaCenter,
                (float)(gammaRight - gammaCenter),
                (float)(gammaDown - gammaCenter));
        }

        private static double GetGamma(ElevationMap dem, double col, double row)
        {
            var (latDeg, lonDeg) = dem.Point2LatLonDeg(col, row);
            var lon = lonDeg * Math.PI / 180.0;
            var lat = latDeg * Math.PI / 180.0;
            var (_, gamma) = MoonSrsLambdaFactory.GetDistortion(new CRSPoint(lon, lat), dem.SrsDescriptor);
            return gamma;
        }

        private static float SampleHorizonElevationDeg(float[] horizons, int horizonBase, float azimuthDeg)
        {
            return SampleHorizonElevationDeg(horizons.AsSpan(horizonBase, LightmapGenerator.HorizonSamples), azimuthDeg);
        }

        private static float SamplePixelHorizonElevationDeg(float[] allHorizons, int x, int y, float azimuthDeg)
        {
            int pixelIdx = y * Width + x;
            return SampleHorizonElevationDeg(allHorizons, pixelIdx * HorizonSamples, azimuthDeg);
        }

        private static float SampleHorizonElevationDeg(ReadOnlySpan<float> horizons, float azimuthDeg)
        {
            const float bucketWidthDeg = 360f / LightmapGenerator.HorizonSamplesF;
            const float bucketHalfWidthDeg = bucketWidthDeg / 2f;

            float azWrapped = azimuthDeg % 360f;
            if (azWrapped < 0f)
                azWrapped += 360f;

            float leftBucketFloat = (azWrapped - bucketHalfWidthDeg) * (LightmapGenerator.HorizonSamplesF / 360f);
            int leftBucket = (int)MathF.Floor(leftBucketFloat);
            float frac = leftBucketFloat - leftBucket;

            while (leftBucket < 0)
                leftBucket += LightmapGenerator.HorizonSamples;
            while (leftBucket >= LightmapGenerator.HorizonSamples)
                leftBucket -= LightmapGenerator.HorizonSamples;

            int rightBucket = leftBucket + 1;
            if (rightBucket >= LightmapGenerator.HorizonSamples)
                rightBucket = 0;

            float left = horizons[leftBucket];
            float right = horizons[rightBucket];
            return left + frac * (right - left);
        }

        private static void WriteFloatTiff(string path, float[] data, ElevationMap dem)
        {
            using var dataset = CreateTiff(path, DataType.GDT_Float32, -9999.0, dem);
            dataset.GetRasterBand(1).WriteRaster(0, 0, Width, Height, data, Width, Height, 0, 0);
            dataset.FlushCache();
        }

        private static void WriteByteTiff(string path, byte[] data, ElevationMap dem)
        {
            using var dataset = CreateTiff(path, DataType.GDT_Byte, 0.0, dem);
            dataset.GetRasterBand(1).WriteRaster(0, 0, Width, Height, data, Width, Height, 0, 0);
            dataset.FlushCache();
        }

        private static Dataset CreateTiff(string path, DataType dataType, double noData, ElevationMap dem)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var driver = Gdal.GetDriverByName("GTiff")
                ?? throw new InvalidOperationException("GDAL GTiff driver is unavailable.");
            return LightmapPipeline.OpenDataset(
                driver,
                path,
                dataType,
                noData,
                Width,
                Height,
                dem.Projection,
                dem.GeoTransform);
        }
    }
}
