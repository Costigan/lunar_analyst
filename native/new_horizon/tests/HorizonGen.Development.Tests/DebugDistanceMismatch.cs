using moonlib.horizon;
using System.Drawing;

namespace moonlib.tests
{
    [TestClass]
    public class DebugDistanceMismatch
    {
        private static ElevationMap MakeStereoDem(int width, int height, double pixelMeters)
        {
            var elev = new float[height, width];
            // Flat DEM (elevation 0)
            // GeoTransform: [originX, pixelSizeX, rotX, originY, rotY, pixelSizeY]
            // Top-Left origin at (0, height * pixelMeters) so (0,0) is bottom-left in Cartesian
            var gt = new double[] { 0.0, pixelMeters, 0.0, height * pixelMeters, 0.0, -pixelMeters };

            // Proj4 string for Stereographic centered at 0,0
            string stereoProj = "+proj=stere +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs";

            return new ElevationMap(elev, stereoProj, gt);
        }

        [TestMethod]
        public void DemonstrateSlopeDivergenceAtDistance()
        {
            // Create a large DEM to allow long distance ray casting
            // 2000x2000 pixels at 1000m resolution = 2000km x 2000km
            int w = 2000;
            int h = 2000;
            double res = 1000.0;
            var dem = MakeStereoDem(w, h, res);

            // Observer at center
            var obs = new PixelOrigin { X = 1000, Y = 1000, Z = 0f };
            double azimuth = 0.0; // North

            // Capture output
            string tempDir = Path.Combine(Path.GetTempPath(), "slope_mismatch_test");
            Directory.CreateDirectory(tempDir);
            
            Console.WriteLine("Running Reference Emulator...");
            var refRes = ReferenceRayEmulator.Run(dem, obs, azimuth, Path.Combine(tempDir, "ref.csv"), suppressCsv: false, unifiedStepMode: false, maxDistanceMeters: 500000);

            Console.WriteLine("Running QuadTree Emulator...");
            var qtRes = QuadTreeRayEmulator.Run(dem, obs, azimuth, Path.Combine(tempDir, "qt.csv"), suppressCsv: false, unifiedStepMode: false);

            // Compare slopes at ~400km by finding samples at the same PIXEL LOCATION
            // Note: Reference uses tangent distance, QuadTree uses chord distance, so we compare by pixel position
            var refSample = refRes.Trace.OrderBy(s => Math.Abs(s.DistanceMeters - 400000)).First();
            
            // Find QuadTree sample at the same pixel location as the reference sample
            var qtSample = qtRes.Trace
                .OrderBy(s => Math.Sqrt(
                    Math.Pow(s.PixelX - refSample.PixelX, 2) + 
                    Math.Pow(s.PixelY - refSample.PixelY, 2)))
                .First();

            Console.WriteLine($"Comparison at pixel ({refSample.PixelX:F1}, {refSample.PixelY:F1}):");
            Console.WriteLine($"Reference: Dist={refSample.DistanceMeters:F1}m (tangent), Slope={refSample.Slope:F6}");
            Console.WriteLine($"QuadTree:  Dist={qtSample.DistanceMeters:F1}m (chord), Slope={qtSample.Slope:F6}");

            // Difference check
            double diff = Math.Abs(refSample.Slope - qtSample.Slope);
            Console.WriteLine($"Slope Difference: {diff:F6}");

            // TDD Approach: This test should Assert that the implementation is CORRECT.
            // Currently, this will FAIL because of the bug.
            // Once fixed, this test will PASS.
            
            // We expect the QuadTree to match the Reference within the error budget (0.1 degrees = ~0.0017 rads)
            Assert.AreEqual(refSample.Slope, qtSample.Slope, 0.1, 
                $"Slopes diverged at {refSample.DistanceMeters:F0}m. Ref: {refSample.Slope:F6}, QT: {qtSample.Slope:F6}. Diff: {diff:F6}");
        }
    }
}
