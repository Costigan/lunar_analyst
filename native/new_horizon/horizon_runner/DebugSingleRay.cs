using System;
using moonlib.horizon;
using moonlib.math;

namespace horizon_runner
{
    public class DebugSingleRay
    {
        public static void Run()
        {
            string demPath = "/d/datasets/viper_v71_2024_medium/other/dem.tif";
            var origin = new PixelOrigin { X = 837, Y = 3280, Z = 0 };
            
            var xEnv = Environment.GetEnvironmentVariable("DEBUG_SINGLE_RAY_X");
            if (!string.IsNullOrWhiteSpace(xEnv) && int.TryParse(xEnv, out var envX))
            {
                origin.X = envX;
            }

            var yEnv = Environment.GetEnvironmentVariable("DEBUG_SINGLE_RAY_Y");
            if (!string.IsNullOrWhiteSpace(yEnv) && int.TryParse(yEnv, out var envY))
            {
                origin.Y = envY;
            }

            double azimuthDeg = 90.0;
            var azEnv = Environment.GetEnvironmentVariable("DEBUG_SINGLE_RAY_AZ");
            if (!string.IsNullOrWhiteSpace(azEnv) && double.TryParse(azEnv, out var envAz))
            {
                azimuthDeg = envAz;
            }

            Console.WriteLine($"Running DebugSingleRay for {demPath}");
            Console.WriteLine($"Origin: {origin.X}, {origin.Y}");
            Console.WriteLine($"Azimuth: {azimuthDeg}");

            var dem = new ElevationMap(demPath);

            // Compare Start Points
            double px = origin.X;
            double py = origin.Y;
            
            // 1. Reference (GDAL/PROJ)
            var (refLat, refLon) = dem.Point2LatLonDeg(px, py);
            Console.WriteLine($"Ref Start Lat/Lon: {refLat}, {refLon}");

            // 2. QuadTree (Manual)
            var srs = dem.SrsDescriptor;
            var proj = new ProjectionParams
            {
                R = (float)srs.R,
                Lat0 = (float)srs.lat0,
                Lon0 = (float)srs.lon0,
                K0 = (float)srs.k0,
                FalseEasting = (float)srs.FalseEasting,
                FalseNorthing = (float)srs.FalseNorthing
            };
            
            // QT MapParams for PixelToCRS
            var map = BuildMapParams(dem);
            var (crsX, crsY) = map.PixelToCRS((float)px, (float)py);
            
            // Float Test
            var (qtLatRad, qtLonRad) = QuadTreeRayEmulator.InverseProject(crsX, crsY, proj);
            double qtLat = qtLatRad * 180.0 / Math.PI;
            double qtLon = qtLonRad * 180.0 / Math.PI;
            
            Console.WriteLine($"QT (Float) Start Lat/Lon: {qtLat}, {qtLon}");
            Console.WriteLine($"Diff Lat (Float): {refLat - qtLat:E5}");
            Console.WriteLine($"Diff Lon (Float): {refLon - qtLon:E5}");

            // Double Test
            var projD = new ProjectionParamsDouble
            {
                R = srs.R,
                Lat0 = srs.lat0,
                Lon0 = srs.lon0,
                K0 = srs.k0,
                FalseEasting = srs.FalseEasting,
                FalseNorthing = srs.FalseNorthing
            };
            // Use double precision for PixelToCRS too (manually here since MapParams is float)
            double det = map.T1 * map.T5 - map.T2 * map.T4; // Approx since T values are float
            // Actually ElevationMap has PixelToCRS which returns CRSPoint (double)
            var crsPtD = dem.PixelToCRS(new PixelPoint(px, py));
            
            var (qtLatRadD, qtLonRadD) = QuadTreeHorizonGenerator.InverseProjectDouble(crsPtD.X, crsPtD.Y, projD);
            double qtLatD = qtLatRadD * 180.0 / Math.PI;
            double qtLonD = qtLonRadD * 180.0 / Math.PI;
            
            Console.WriteLine($"QT (Double) Start Lat/Lon: {qtLatD}, {qtLonD}");
            Console.WriteLine($"Diff Lat (Double): {refLat - qtLatD:E5}");
            Console.WriteLine($"Diff Lon (Double): {refLon - qtLonD:E5}");


            Console.WriteLine("Running ReferenceRayEmulator...");
            var refResult = ReferenceRayEmulator.Run(dem, origin, azimuthDeg, "/d/projects/new_horizon/reference_trace.csv");

            Console.WriteLine("Running QuadTreeRayEmulator...");
            var qtResult = QuadTreeRayEmulator.Run(dem, origin, azimuthDeg, "/d/projects/new_horizon/quadtree_trace.csv", logCoefficients: true);

            Console.WriteLine("Done.");
        }

        static MapParams BuildMapParams(ElevationMap dem)
        {
            var srs = dem.SrsDescriptor;
            var geo = dem.GeoTransform;
            var colStepX = (float)geo[1];
            var rowStepX = (float)geo[2];
            var colStepY = (float)geo[4];
            var rowStepY = (float)geo[5];
            var det = colStepX * rowStepY - rowStepX * colStepY;
            var invDet = 1f / det;
            return new MapParams(
                (float)srs.R, (float)srs.k0, (float)srs.FalseEasting, (float)srs.FalseNorthing, invDet,
                (float)geo[0], (float)geo[1], (float)geo[2], (float)geo[3], (float)geo[4], (float)geo[5]);
        }
    }
}
