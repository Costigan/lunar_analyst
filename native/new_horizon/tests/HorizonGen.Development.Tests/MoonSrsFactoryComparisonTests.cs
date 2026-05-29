using Microsoft.VisualStudio.TestTools.UnitTesting;
using moonlib;
using moonlib.horizon;
using moonlib.math;
using System;
using System.IO;

namespace moonlib.tests
{
    [TestClass]
    public class MoonSrsFactoryComparisonTests
    {
        [TestMethod]
        public void CompareFactoryVsOsr_AllDems()
        {
            var demPaths = new[]
            {
                "/d/datasets/viper_v71_2024_medium/other/dem.tif",
                "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
		"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
            };

            foreach (var path in demPaths)
            {
                if (!File.Exists(path))
                {
                    Console.WriteLine($"Skipping missing DEM: {path}");
                    continue;
                }

                Console.WriteLine($"Testing DEM: {Path.GetFileName(path)}");
                var dem = new ElevationMap(path, loadRaster: false);

                // Define test points: Corners and Center (in Pixel Coordinates)
                var points = new[]
                {
                    new PixelPoint(0, 0),
                    new PixelPoint(dem.Width, 0),
                    new PixelPoint(0, dem.Height),
                    new PixelPoint(dem.Width, dem.Height),
                    new PixelPoint(dem.Width / 2.0, dem.Height / 2.0)
                };

                foreach (var ptPixel in points)
                {
                    // Convert Pixel -> CRS
                    var ptCrs = dem.PixelToCRS(ptPixel);

                    // 1. Factory
                    var factoryResult = dem.SrsToLongLat(ptCrs);
                    double factoryLon = factoryResult.X.ToDegrees();
                    double factoryLat = factoryResult.Y.ToDegrees();

                    // 2. OSR
                    var osrResult = dem.SrsToLongLatReference(ptCrs);
                    double osrLon = osrResult.X.ToDegrees();
                    double osrLat = osrResult.Y.ToDegrees();

                    // Normalize Longitude to -180..180 for comparison
                    factoryLon = NormalizeLon(factoryLon);
                    osrLon = NormalizeLon(osrLon);

                    Console.WriteLine($"  Pt {ptPixel} -> CRS {ptCrs}:");
                    Console.WriteLine($"    Factory: ({factoryLat:F5}, {factoryLon:F5})");
                    Console.WriteLine($"    OSR:     ({osrLat:F5}, {osrLon:F5})");

                    Assert.AreEqual(osrLat, factoryLat, 0.001, $"Lat mismatch for {Path.GetFileName(path)} at {ptPixel}");
                    // Longitude can be tricky near poles, but let's try strict check first
                    // If near pole, longitude delta might be large even if points are close.
                    if (Math.Abs(osrLat) < 89.9) 
                        Assert.AreEqual(osrLon, factoryLon, 0.001, $"Lon mismatch for {Path.GetFileName(path)} at {ptPixel}");
                }
            }
        }

        private double NormalizeLon(double lon)
        {
            while (lon <= -180) lon += 360;
            while (lon > 180) lon -= 360;
            return lon;
        }
    }
}
