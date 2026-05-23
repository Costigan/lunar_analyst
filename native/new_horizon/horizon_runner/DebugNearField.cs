using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using moonlib;
using moonlib.horizon;

namespace horizon_runner
{
    public class DebugNearField
    {
        public static void Run()
        {
            var demPaths = new List<string>
            {
                "/d/datasets/viper_v71_2024_medium/other/dem.tif",
                "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
                "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
            };

            // Use environment variables or defaults
            int x = 837;
            int y = 3280;
            float observerZ = 0f;
            double azimuth = 90.0;
            float clampMeters = 50f;

            var xEnv = Environment.GetEnvironmentVariable("DEBUG_X");
            if (int.TryParse(xEnv, out int px)) x = px;

            var yEnv = Environment.GetEnvironmentVariable("DEBUG_Y");
            if (int.TryParse(yEnv, out int py)) y = py;
            
            var zEnv = Environment.GetEnvironmentVariable("DEBUG_Z");
            if (float.TryParse(zEnv, out float pz)) observerZ = pz;

            var azEnv = Environment.GetEnvironmentVariable("DEBUG_AZ");
            if (double.TryParse(azEnv, out double az)) azimuth = az;

            var clampEnv = Environment.GetEnvironmentVariable("QUADTREE_NEARFIELD_METERS");
            if (float.TryParse(clampEnv, out float cm)) clampMeters = cm;

            Console.WriteLine($"Running DebugNearField...");
            Console.WriteLine($"  Point: ({x}, {y})");
            Console.WriteLine($"  Observer Z: {observerZ}");
            Console.WriteLine($"  Azimuth: {azimuth}");
            Console.WriteLine($"  Clamp Meters: {clampMeters}");

            var dems = demPaths.Select(p => new ElevationMap(p)).ToList();
            var origin = new PixelOrigin { X = x, Y = y, Z = observerZ };

            string outputPath = Path.Combine(Directory.GetCurrentDirectory(), "near_field_trace.csv");
            
            try
            {
                var slopes = NearFieldRayEmulator.Run(
                    dems, 
                    origin, 
                    azimuth, 
                    clampMeters, 
                    observerZ, 
                    outputPath
                );

                Console.WriteLine($"Trace written to {outputPath}");
                Console.WriteLine($"Total samples: {slopes.Length}");
                if (slopes.Length > 0)
                {
                    double maxSlope = slopes.Max();
                    Console.WriteLine($"Max Slope: {maxSlope} ({(Math.Atan(maxSlope) * 180 / Math.PI):F4} deg)");
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Error running emulator: {ex.Message}");
                Console.WriteLine(ex.StackTrace);
            }
        }
    }
}
