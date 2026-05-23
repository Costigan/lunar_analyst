using moonlib.horizon;
using moonlib.spice;
using System.Drawing;
using System.Drawing.Imaging;

#nullable disable

namespace moonlib.tests
{
    /// <summary>
    /// Tests to validate Compact Mode polynomial approximation accuracy
    /// by comparing large patch results vs single-pixel results.
    /// </summary>
    [TestClass]
    public class LightmapRegression
    {
        private static readonly string[] DemPaths = new[] {
            @"/d/datasets/viper_v71_2024_medium/other/dem.tif",
            @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
            @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
        };

        const string ReferencePNG = @"/d/projects/new_horizon/tests/test_data/test_lightmap.png";

        [TestMethod]
        public void DetectLightmapRegressions()
        {
            var spiceManager = SpiceManager.Singleton;
            // sun_image_2014-07-24T09-34-40
            var time = new DateTime(2014, 07, 24, 9, 34, 40, DateTimeKind.Utc);
            var sunvec = SpiceManager.SunPosition(time) * 1000.0;

            var dem_paths = new[] { @"/d/projects/new_horizon/tests/test_data/test_dem.tif" }.Concat(DemPaths).ToList();
            if (!dem_paths.All(File.Exists))
            {
                Assert.Inconclusive($"Required DEMs not found. Expected: {string.Join(", ", dem_paths)}");
                return;
            }

            var dems = dem_paths.Select(path => new ElevationMap(path)).ToList();
            Assert.AreEqual(4, dems.Count, "Expected 4 DEMs for regression test.");

            var inner_dem = dems[0];
            Assert.IsNotNull(inner_dem, "Failed to load inner DEM for regression test.");
            Assert.IsTrue(inner_dem.Width == 128 && inner_dem.Height == 128, $"Expected inner DEM to be 128x128. Got {inner_dem?.Width}x{inner_dem?.Height}.");

            // Generate horizons for selected patches
            float observerElevation = 0f;
            using var generator = new QuadTreeHorizonGenerator();
            var angles = generator.GenerateHorizons(dems, 0, 0, 128, 128, observerElevation);

            Assert.IsNotNull(angles, "Horizon angles generation failed.");
            Assert.AreEqual(1440 * 128 * 128, angles.Degrees.Length, $"Expected horizon array length of {1440 * 128 * 128}. Got {angles.Degrees.Length}.");

            var horizons = angles.Degrees;
            var lightmap = new byte[128, 128];
            for (int row = 0; row < 128; row++)
                for (int col = 0; col < 128; col++)
                {
                    var mat = inner_dem.GetMoonMEToENU(row, col);
                    var (az_rad, el_rad) = inner_dem.GetAzEl(sunvec, mat);

                    float az_deg = az_rad * 57.2957795f;
                    float el_deg = el_rad * 57.2957795f;
                    int pixelIdx = row * 128 + col;
                    float frac = LightmapGenerator.BuilderSunFraction(horizons, pixelIdx * 1440, az_deg, el_deg);
                    lightmap[row, col] = (byte)(frac * 255);
                }

#if false
            {
                // Write PNG
                using var bmp = new Bitmap(128, 128);
                for (int row = 0; row < 128; row++)
                    for (int col = 0; col < 128; col++)
                    {
                        byte intensity = lightmap[row, col];
                        bmp.SetPixel(col, row, Color.FromArgb(intensity, intensity, intensity));
                    }
                bmp.Save(ReferencePNG, ImageFormat.Png);
            }
#else
            {
                // Compare lightmap to reference PNG
                var reference = new Bitmap(ReferencePNG);
                for (int row = 0; row < 128; row++)
                    for (int col = 0; col < 128; col++)
                    {
                        byte intensity = lightmap[row, col];
                        var refval =  reference.GetPixel(col, row);
                        Assert.AreEqual(refval.R, intensity, $"Red channel mismatch at ({col}, {row}). Expected {refval.R}, got {intensity}.");
                        Assert.AreEqual(refval.G, intensity, $"Green channel mismatch at ({col}, {row}). Expected {refval.G}, got {intensity}.");
                        Assert.AreEqual(refval.B, intensity, $"Blue channel mismatch at ({col}, {row}). Expected {refval.B}, got {intensity}.");
                    }
            }
#endif

        }
    }
}
