using moonlib;
using moonlib.horizon;

namespace HorizonGen.Tests
{
    [TestClass]
    /// <summary>
    /// Integration tests for the <see cref="QuadTreeHorizonGenerator"/>.
    /// These tests verify the end-to-end generation of horizon profiles on synthetic terrain (flat, simple obstacles)
    /// to ensure the ray-casting kernel and quadtree traversal logic are functioning correctly.
    /// </summary>
    public class QuadTreeHorizonGeneratorTests
    {
        // Using North Polar Stereographic for Moon
        private const string StereographicProj4 = @"+proj=stere +lat_0=90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +no_defs";
        private const double MoonRadius = 1737400.0; // meters

        /// <summary>
        /// Verifies that on a perfectly flat Moon, the generated horizon angles are consistently negative.
        /// This is due to the curvature of the Moon; a flat tangent plane would be at elevation 0,
        /// but the surface drops away from the observer.
        /// </summary>
        [TestMethod]
        [TestCategory("Fast")]
        public void GenerateHorizons_FlatTerrain_ReturnsConsistentNegativeHorizon()
        {
            // Arrange
            int demSize = 3;
            float[,] flatElevation = new float[demSize, demSize]; // All elevations are 0 meters
            // Fill with 0s explicitly
            for (int r = 0; r < demSize; r++)
            {
                for (int c = 0; c < demSize; c++)
                {
                    flatElevation[r, c] = 0.0f;
                }
            }

            double[] geoTransform = new double[]
            {
                -(demSize / 2.0) * 100.0, // Top-left X
                100.0,                    // X pixel size
                0,                            // Rotation
                (demSize / 2.0) * 100.0,  // Top-left Y
                0,                            // Rotation
                -100.0                    // Y pixel size (negative for North-up)
            };
            
            // Create in-memory ElevationMap. Path is null so it won't try to cache.
            var innerDem = new ElevationMap(flatElevation, StereographicProj4, geoTransform);
            var dems = new List<ElevationMap> { innerDem };

            // Observer at the center pixel of the DEM (pixel coordinate (1,1))
            // The GenerateHorizons method handles observer height offset (hardcoded +2.0f)
            int observerCol = 1; 
            int observerRow = 1;
            int tileWidth = 1; // Test a single pixel
            int tileHeight = 1;

            string outputDir = Path.Combine(Path.GetTempPath(), "QuadTreeHorizonGeneratorTests");
            if (!Directory.Exists(outputDir))
                Directory.CreateDirectory(outputDir);

            // Act
            using var generator = new QuadTreeHorizonGenerator();
            // Call the new overload that accepts List<ElevationMap>
            generator.GenerateHorizons(outputDir, dems, observerCol, observerRow, tileWidth, tileHeight);

            // Assert
            // The horizon should be consistent and negative due to the curvature of the Moon
            // Horizon data is stored as raw float values representing the elevation angle in radians.
            // A negative angle means the horizon is below the geometric horizontal plane.
            var horizonFilePath = new HorizonTileStore(outputDir)
                .BuildPath(observerRow, observerCol, 0f, compress: false);
            Assert.IsTrue(File.Exists(horizonFilePath), $"Horizon file was not created at {horizonFilePath}.");

            float[] horizons = Utilities.LoadBinaryArray<float>(horizonFilePath);
            Assert.AreEqual(1 * 1 * 1440, horizons.Length); // 1 pixel, 1440 azimuth samples

            float firstHorizonValue = horizons[0];
            Assert.IsTrue(float.IsFinite(firstHorizonValue), "Horizon value should be a finite number.");
            Assert.IsTrue(firstHorizonValue < 0, "Horizon value should be negative for flat terrain due to planetary curvature.");

            // Check if all horizon values are negative (consistent with flat terrain on a sphere)
            // We don't check for equality because the distance to the edge of a square DEM varies with azimuth.
            for (int i = 0; i < horizons.Length; i++)
            {
                Assert.IsTrue(horizons[i] < 0, $"Horizon value at index {i} should be negative (was {horizons[i]}).");
            }

            // Cleanup
            Directory.Delete(outputDir, true);
        }

        /// <summary>
        /// Verifies that a single tall obstacle placed on flat terrain produces a positive horizon peak
        /// at the correct azimuth and matches the theoretically calculated slope.
        /// </summary>
        [TestMethod]
        [TestCategory("Fast")]
        public void GenerateHorizons_SingleObstacle_ReturnsPositiveHorizonAtObstacleAzimuth()
        {
            // Arrange
            int demSize = 10;
            float[,] terrain = new float[demSize, demSize]; 
            // Flat terrain with a wall obstacle at columns 8 and 9
            for (int r = 0; r < demSize; r++)
            {
                for (int c = 0; c < demSize; c++)
                {
                    if (c == 8 || c == 9)
                        terrain[r, c] = 100.0f;
                    else
                        terrain[r, c] = 0.0f;
                }
            }

            double pixelSize = 100.0; // meters per pixel
            double[] geoTransform = new double[]
            {
                -(demSize / 2.0) * pixelSize, // Top-left X
                pixelSize,                    // X pixel size
                0,                            // Rotation
                (demSize / 2.0) * pixelSize,  // Top-left Y
                0,                            // Rotation
                -pixelSize                    // Y pixel size (negative for North-up)
            };
            
            var innerDem = new ElevationMap(terrain, StereographicProj4, geoTransform);
            var dems = new List<ElevationMap> { innerDem };

            int observerCol = 5; 
            int observerRow = 5; // Observer exactly at center (5,5)
            int tileWidth = 1; 
            int tileHeight = 1;

            string outputDir = Path.Combine(Path.GetTempPath(), "QuadTreeHorizonGeneratorTests_Obstacle");
            if (!Directory.Exists(outputDir))
                Directory.CreateDirectory(outputDir);

            // Act
            using var generator = new QuadTreeHorizonGenerator();
            generator.GenerateHorizons(outputDir, dems, observerCol, observerRow, tileWidth, tileHeight, 2.0f);

            // Assert
            var horizonFilePath = new HorizonTileStore(outputDir)
                .BuildPath(observerRow, observerCol, 2.0f, compress: false);
            Assert.IsTrue(File.Exists(horizonFilePath), $"Horizon file was not created at {horizonFilePath}.");

            float[] horizons = Utilities.LoadBinaryArray<float>(horizonFilePath);
            Assert.AreEqual(1440, horizons.Length); 

            // Calculate expected azimuth of the obstacle relative to observer
            // Observer (5,5), Obstacle (5,8)
            // Delta Y = 8 - 5 = 3 pixels (North direction in image coords is decreasing row, so this means it's 'further' in the positive Y direction in CRS)
            // Delta X = 5 - 5 = 0 pixels
            // In a North-up system, (0,1) is North, (1,0) is East.
            // Our geoTransform means positive X is right, negative Y is down (increasing row).
            // So from (5,5) to (5,8): X delta = 0, Y delta = 3*pixelSize (in CRS units)
            // This is pointing due South in CRS Y-axis, which is 180 degrees.
            // But the kernel `azimuth = XMath.Atan2(y, x) + XMath.PI;
            // X corresponds to dirX, Y to dirY.
            // For a point (5,8) from (5,5): deltaX=0, deltaY=3.
            // XMath.Atan2(3, 0) is PI/2. So azimuth is PI/2 + PI = 3PI/2 = 270 degrees (West) in horizon bins.
            // Let's re-verify the azimuth conversion.
            // dirX = XMath.Cos(gridAzRad); dirY = XMath.Sin(gridAzRad);
            // If azRad is 0, dirX=1, dirY=0 (East)
            // If azRad is PI/2, dirX=0, dirY=1 (North)
            // If azRad is PI, dirX=-1, dirY=0 (West)
            // If azRad is 3PI/2, dirX=0, dirY=-1 (South)
            // Obstacle pixel (5,8)
            // Observer pixel (5,5)
            // This is relative movement in pixel space.
            // The CRS mapping will turn this into a direction.
            // For Pixel (5,8) from (5,5), it's 3 units in the `col` direction.
            // This needs to be converted to real-world coordinates then to azimuth.
            // Pixel (col, row) to CRS: x = T0 + T1*col + T2*row, y = T3 + T4*col + T5*row
            // Observer CRS: (obsX, obsY) = map.PixelToCRS(observerCol, observerRow)
            // Obstacle CRS: (obsX + deltaX, obsY + deltaY) = map.PixelToCRS(5, 8)
            // The `QuadTreeRayCastKernel` uses `dirX` and `dirY` which are based on `gridAzRad`.
            // The gridAzRad is 0 for 0 degree azimuth.
            // The azimuth index calculation in the kernel: `horizon_index = (int)(0.5f + azimuth * (HorizonSamples - 1) / (2f * XMath.PI));`
            // HorizonSamples = 1440. Max azimuth is 2PI.
            // So index 0 is 0 deg, index 360 is 90 deg, index 720 is 180 deg, index 1080 is 270 deg.
            // The obstacle is at (col=8, row=5) from observer (col=5, row=5).
            // This is 3 units to the right in `col` (image X) coordinates.
            // With geoTransform, `x = T0 + T1*col + T2*row`. T1 is pixelSize, T2 is 0. So positive X is east.
            // `y = T3 + T4*col + T5*row`. T4 is 0, T5 is -pixelSize. So positive Y is south.
            // A move from (5,5) to (8,5) is a move in positive X direction. This corresponds to 0 degree azimuth (East).
            // So the obstacle is at 0 degrees azimuth.
            // `XMath.Atan2(0, positive_val) = 0`. So `azimuth = 0 + XMath.PI = PI`.
            // So the azimuth is 180 degrees (West). Index = 720.

            // Let's re-examine `QuadTreeRayCastKernel` azimuth handling
            // `dirX = XMath.Cos(gridAzRad); dirY = XMath.Sin(gridAzRad);`
            // `azRad = azIdx * (2.0f * XMath.PI / 1440.0f);`
            // So azIdx=0 => azRad=0 (East)
            // azIdx=360 => azRad=PI/2 (North)
            // azIdx=720 => azRad=PI (West)
            // azIdx=1080 => azRad=3PI/2 (South)

            // Observer (5,5), obstacle (5,8). This is 3 pixels to the right in the grid image (larger column index).
            // This means we are moving in the positive X direction (East).
            // So obstacle is at 0 degrees azimuth from the observer. This is `azIdx = 0`.
            // However, the internal kernel uses `azimuth = XMath.Atan2(y, x) + XMath.PI;`
            // For East (x=positive, y=0), Atan2(0, pos) is 0. So azimuth = PI.
            // So the obstacle is at PI (180 degrees) from the perspective of the internal coordinate system for azimuth calculation.
            // This maps to `horizon_index = (int)(0.5f + PI * (1440 - 1) / (2f * PI)) = (int)(0.5f + (1439/2)) = (int)(0.5 + 719.5) = 720`.
            // So the peak should be at index 720.

            // Get distance to obstacle
            float obsX_crs = (float)(geoTransform[0] + geoTransform[1] * observerCol + geoTransform[2] * observerRow);
            float obsY_crs = (float)(geoTransform[3] + geoTransform[4] * observerCol + geoTransform[5] * observerRow);
            float obsZ = (float)innerDem.GetElevation(observerCol, observerRow) + 2.0f; // Observer height

            float obstacleCol = 8;
            float obstacleRow = 5;
            float obstacleX_crs = (float)(geoTransform[0] + geoTransform[1] * obstacleCol + geoTransform[2] * obstacleRow);
            float obstacleY_crs = (float)(geoTransform[3] + geoTransform[4] * obstacleCol + geoTransform[5] * obstacleRow);
            float obstacleZ = (float)innerDem.GetElevation(obstacleCol, obstacleRow);

            float dist_map = (float)Math.Sqrt(Math.Pow(obstacleX_crs - obsX_crs, 2) + Math.Pow(obstacleY_crs - obsY_crs, 2));
            
            // Recompute k_factor and convergence for precise slope calculation
            var centerPixel = new PixelPoint(observerCol, observerRow);
            var centerCrs = innerDem.PixelToCRS(centerPixel);
            var centerLonLatRad = innerDem.SrsToLongLat(centerCrs);
            var (k_factor_calc, gamma_calc) = MoonSrsLambdaFactory.GetDistortion(centerLonLatRad, innerDem.SrsDescriptor);
            float k_factor = (float)k_factor_calc;

            float dist_ground = dist_map / k_factor;
            float drop = (dist_ground * dist_ground) / (2.0f * (float)MoonRadius);
            float h_eff_obstacle = obstacleZ - drop;

            // Slope to the obstacle from observer (relative to tangent plane at observer)
            float expected_slope = (h_eff_obstacle - obsZ) / dist_ground;
            float expected_angle = (float)Math.Atan(expected_slope);

            // Check if the expected azimuth has a higher slope than others
            int expectedPeakAzimuthIndex = 360; // East direction (90 degrees)

            // Find the maximum slope in the horizons array
            float maxObservedAngle = float.NegativeInfinity;
            int maxSlopeIndex = -1;
            for(int i = 0; i < horizons.Length; i++)
            {
                if (horizons[i] > maxObservedAngle)
                {
                    maxObservedAngle = horizons[i];
                    maxSlopeIndex = i;
                }
            }
            Console.WriteLine($"DEBUG: Max Index: {maxSlopeIndex}, Max Angle: {maxObservedAngle}");
            Console.WriteLine($"DEBUG: Value at 360 (Expected): {horizons[360]}");
            if (horizons.Length > 305) Console.WriteLine($"DEBUG: Value at 305 (Old Peak): {horizons[305]}");
            if (horizons.Length > 414) Console.WriteLine($"DEBUG: Value at 414 (New Peak): {horizons[414]}");

            // Assert that the max slope is positive (above horizon) and at the expected azimuth
            Assert.IsTrue(maxObservedAngle > 0, $"Max observed angle should be positive, but was {maxObservedAngle}.");
            Assert.AreEqual(expectedPeakAzimuthIndex, maxSlopeIndex, "Peak horizon not at expected azimuth.");
            Assert.AreEqual(expected_angle, maxObservedAngle, 0.05f, "Observed max angle does not match calculated expected angle.");


            // Also check that other values are generally lower
            float flatTerrainHorizon = horizons.Where((h, idx) => idx != maxSlopeIndex).Average(); // Average of non-peak horizons
            Assert.IsTrue(flatTerrainHorizon < maxObservedAngle, "Other horizon values should be lower than the peak.");
            
            Directory.Delete(outputDir, true); // Clean up
        }
    }
}
