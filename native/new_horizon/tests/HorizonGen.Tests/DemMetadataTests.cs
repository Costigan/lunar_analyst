using moonlib;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using System;
using System.IO;
using moonlib.horizon;

namespace moonlib.tests
{
    [TestClass]
    public class DemMetadataTests
    {
        private const string DemPath = @"/d/datasets/viper_v71_2024_medium/other/dem.tif";

        [TestMethod]
        public void CheckMetadata()
        {
            if (!File.Exists(DemPath))
            {
                Assert.Inconclusive($"DEM file not found at {DemPath}");
            }

            var dem = new ElevationMap(DemPath);
            var geo = dem.GeoTransform;
            Console.WriteLine($"GeoTransform: {string.Join(", ", geo)}");
            
            double pixCol = Math.Sqrt(geo[1] * geo[1] + geo[4] * geo[4]);
            double pixRow = Math.Sqrt(geo[2] * geo[2] + geo[5] * geo[5]);
            Console.WriteLine($"PixCol: {pixCol}");
            Console.WriteLine($"PixRow: {pixRow}");
            Console.WriteLine($"MapRes: {(pixCol + pixRow) * 0.5}");
            
            var srs = dem.SrsDescriptor;
            Console.WriteLine($"SRS R: {srs.R}");
            Console.WriteLine($"SRS k0: {srs.k0}");
            Console.WriteLine($"SRS lat0: {srs.lat0}");
            Console.WriteLine($"SRS lon0: {srs.lon0}");
        }
    }
}
