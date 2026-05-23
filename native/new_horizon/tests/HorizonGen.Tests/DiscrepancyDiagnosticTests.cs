using Microsoft.VisualStudio.TestTools.UnitTesting;
using moonlib.horizon;
using System.Text.Json;

namespace HorizonGen.Tests
{
    /// <summary>
    /// Diagnostic tests to investigate the 90° horizon bug at azimuth 51.75°.
    /// These tests output detailed CSV traces for analysis.
    /// </summary>
    [TestClass]
    public class DiscrepancyDiagnosticTests
    {
        // Hardcoded from the discrepancy report
        private const int ObserverX = 4105;
        private const int ObserverY = 3166;
        private const float ObserverZ = 0f;
        private const double ProblemAzimuth = 51.75; // Index 207 * 0.25

        private static readonly string[] DemPaths = new[]
        {
            @"/d/datasets/viper_v71_2024_medium/other/dem.tif",
            @"/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
            @"/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
        };

        private static readonly string OutputDir = @"/d/projects/new_horizon/output_debug";

        //[TestMethod]  // works but is slow
        public void DiagnoseAzimuth51_75_DetailedTraces()
        {
            var validDemPaths = DemPaths.Where(File.Exists).ToList();
            if (validDemPaths.Count == 0)
            {
                Assert.Inconclusive("No DEMs found. Cannot run diagnostic.");
            }

            Directory.CreateDirectory(OutputDir);
            var dems = validDemPaths.Select(p => new ElevationMap(p)).ToList();
            var origin = new PixelOrigin { X = ObserverX, Y = ObserverY, Z = ObserverZ };

            Console.WriteLine($"=== Diagnostic for Azimuth {ProblemAzimuth}° ===");
            Console.WriteLine($"Observer: pixel ({ObserverX}, {ObserverY}), Z offset = {ObserverZ}m");
            Console.WriteLine($"DEMs loaded: {validDemPaths.Count}");
            foreach (var dem in dems)
            {
                var geo = dem.GeoTransform;
                double res = Math.Sqrt(geo[1] * geo[1] + geo[4] * geo[4]);
                Console.WriteLine($"  - {dem.Width}x{dem.Height}, res={res:F2}m");
            }

            // Run Reference Emulator with CSV output
            Console.WriteLine("\n--- Reference Emulator (Multi-DEM) ---");
            var refResults = new List<EmulatorResult>();
            double refStartDist = 1.0;
            for (int i = 0; i < dems.Count; i++)
            {
                var dem = dems[i];
                PixelOrigin demOrigin;
                if (i == 0)
                {
                    demOrigin = origin;
                }
                else
                {
                    // Transform origin to this DEM's coordinate space
                    var firstDem = dems[0];
                    var (obsLat, obsLon) = firstDem.Point2LatLonDeg(origin.X, origin.Y);
                    var (newRow, newCol) = dem.LonLatDeg2RowCol(obsLon, obsLat);
                    demOrigin = new PixelOrigin
                    {
                        X = (float)newCol,
                        Y = (float)newRow,
                        Z = origin.Z
                    };
                }

                var csvPath = Path.Combine(OutputDir, $"diag_ref_dem{i}.csv");
                var result = ReferenceRayEmulator.Run(
                    dem, demOrigin, ProblemAzimuth, csvPath,
                    suppressCsv: false, unifiedStepMode: false,
                    maxDistanceMeters: 1000000, startDistanceMeters: refStartDist);
                refResults.Add(result);

                Console.WriteLine($"  DEM{i}: {result.Trace.Count} samples, start={refStartDist:F1}m");
                if (result.Trace.Count > 0)
                {
                    var first = result.Trace.First();
                    var last = result.Trace.Last();
                    var maxSlope = result.Slopes.Max();
                    Console.WriteLine($"    First: dist={first.DistanceMeters:F1}m, elev={first.ElevationMeters:F1}m, px=({first.PixelX:F2},{first.PixelY:F2})");
                    Console.WriteLine($"    Last:  dist={last.DistanceMeters:F1}m, elev={last.ElevationMeters:F1}m, px=({last.PixelX:F2},{last.PixelY:F2})");
                    Console.WriteLine($"    MaxSlope={maxSlope:F6} => {Math.Atan(maxSlope) * 180 / Math.PI:F4}°");
                    refStartDist = last.DistanceMeters;
                }
            }
            var refCombined = EmulatorResult.Combine(refResults);
            Console.WriteLine($"  COMBINE/d MaxElevation = {refCombined.ElevationDeg:F4}°");

            // Run QuadTree Emulator with CSV output
            Console.WriteLine("\n--- QuadTree Emulator (Multi-DEM) ---");
            var qtResults = new List<EmulatorResult>();
            double qtStartDist = 1.0;
            for (int i = 0; i < dems.Count; i++)
            {
                var dem = dems[i];
                PixelOrigin demOrigin;
                if (i == 0)
                {
                    demOrigin = origin;
                }
                else
                {
                    var firstDem = dems[0];
                    var (obsLat, obsLon) = firstDem.Point2LatLonDeg(origin.X, origin.Y);
                    var (newRow, newCol) = dem.LonLatDeg2RowCol(obsLon, obsLat);
                    demOrigin = new PixelOrigin
                    {
                        X = (float)newCol,
                        Y = (float)newRow,
                        Z = origin.Z
                    };
                }

                var csvPath = Path.Combine(OutputDir, $"diag_qt_dem{i}.csv");
                var result = QuadTreeRayEmulator.Run(
                    dem, demOrigin, ProblemAzimuth, csvPath,
                    suppressCsv: false, unifiedStepMode: false,
                    logCoefficients: true, startDistanceMeters: qtStartDist);
                qtResults.Add(result);

                Console.WriteLine($"  DEM{i}: {result.Trace.Count} samples, start={qtStartDist:F1}m");
                if (result.Trace.Count > 0)
                {
                    var first = result.Trace.First();
                    var last = result.Trace.Last();
                    var maxSlope = result.Slopes.Max();
                    Console.WriteLine($"    First: dist={first.DistanceMeters:F1}m, elev={first.ElevationMeters:F1}m, px=({first.PixelX:F2},{first.PixelY:F2})");
                    Console.WriteLine($"    Last:  dist={last.DistanceMeters:F1}m, elev={last.ElevationMeters:F1}m, px=({last.PixelX:F2},{last.PixelY:F2})");
                    Console.WriteLine($"    MaxSlope={maxSlope:F6} => {Math.Atan(maxSlope) * 180 / Math.PI:F4}°");
                    qtStartDist = last.DistanceMeters;
                }
                else
                {
                    Console.WriteLine($"    *** NO SAMPLES! ***");
                }
            }
            var qtCombined = EmulatorResult.Combine(qtResults);
            Console.WriteLine($"  COMBINED: MaxElevation = {qtCombined.ElevationDeg:F4}°");

            // Compare first few samples from each
            Console.WriteLine("\n--- First 10 Sample Comparison (DEM0 only) ---");
            var refTrace0 = refResults[0].Trace;
            var qtTrace0 = qtResults[0].Trace;
            Console.WriteLine("Ref:                                          | QT:");
            Console.WriteLine("Dist(m)    Elev(m)    Slope      Px,Py        | Dist(m)    Elev(m)    Slope      Px,Py");
            for (int i = 0; i < Math.Min(10, Math.Max(refTrace0.Count, qtTrace0.Count)); i++)
            {
                var refStr = i < refTrace0.Count
                    ? $"{refTrace0[i].DistanceMeters,8:F1}  {refTrace0[i].ElevationMeters,8:F1}  {refTrace0[i].Slope,10:F6}  ({refTrace0[i].PixelX:F1},{refTrace0[i].PixelY:F1})"
                    : "(no sample)";
                var qtStr = i < qtTrace0.Count
                    ? $"{qtTrace0[i].DistanceMeters,8:F1}  {qtTrace0[i].ElevationMeters,8:F1}  {qtTrace0[i].Slope,10:F6}  ({qtTrace0[i].PixelX:F1},{qtTrace0[i].PixelY:F1})"
                    : "(no sample)";
                Console.WriteLine($"{refStr,-45} | {qtStr}");
            }

            // Check for infinite slopes or NaN values
            Console.WriteLine("\n--- Checking for Anomalies ---");
            var anomalies = qtCombined.Trace
                .Where(t => double.IsInfinity(t.Slope) || double.IsNaN(t.Slope) || t.Slope > 100)
                .Take(5)
                .ToList();
            if (anomalies.Count > 0)
            {
                Console.WriteLine("Found anomalous slopes:");
                foreach (var a in anomalies)
                {
                    Console.WriteLine($"  dist={a.DistanceMeters:F1}m, slope={a.Slope}, elev={a.ElevationMeters:F1}m");
                }
            }
            else
            {
                Console.WriteLine("No infinite/NaN slopes found in QT trace.");
            }

            // Check if QT trace is empty
            if (qtCombined.Trace.Count == 0)
            {
                Console.WriteLine("\n*** CRITICAL: QT trace is EMPTY! BuildRaySamples may have failed. ***");
            }

            // Output summary for easy comparison
            Console.WriteLine($"\n=== SUMMARY ===");
            Console.WriteLine($"Reference Horizon: {refCombined.ElevationDeg:F4}°");
            Console.WriteLine($"QuadTree Horizon:  {qtCombined.ElevationDeg:F4}°");
            Console.WriteLine($"Discrepancy:       {Math.Abs(refCombined.ElevationDeg - qtCombined.ElevationDeg):F4}°");
            Console.WriteLine($"\nCSV traces written to: {OutputDir}");

            // This test is for diagnostics only - don't fail
            // Assert.IsTrue(true);
        }

        [TestMethod]
        public void DiagnoseObserverSetup()
        {
            // Check if the observer position is valid in DEM0
            var demPath = DemPaths.FirstOrDefault(File.Exists);
            if (demPath == null)
            {
                Assert.Inconclusive("No DEMs found.");
            }

            var dem = new ElevationMap(demPath);
            Console.WriteLine($"DEM: {demPath}");
            Console.WriteLine($"  Size: {dem.Width} x {dem.Height}");
            var geo = dem.GeoTransform;
            double mapRes = Math.Sqrt(geo[1] * geo[1] + geo[4] * geo[4]);
            Console.WriteLine($"  GeoTransform: [{geo[0]:F2}, {geo[1]:F6}, {geo[2]:F6}, {geo[3]:F2}, {geo[4]:F6}, {geo[5]:F6}]");
            Console.WriteLine($"  Resolution: {mapRes:F6}m/pixel");
            Console.WriteLine($"  R (sphere radius): {dem.SrsDescriptor?.R ?? 0:F1}m");
            Console.WriteLine($"  lat0: {(dem.SrsDescriptor?.lat0 ?? 0) * 180 / Math.PI:F4}°");
            Console.WriteLine($"  lon0: {(dem.SrsDescriptor?.lon0 ?? 0) * 180 / Math.PI:F4}°");

            // Check if observer is within bounds
            bool inBounds = ObserverX >= 0 && ObserverX < dem.Width &&
                            ObserverY >= 0 && ObserverY < dem.Height;
            Console.WriteLine($"  Observer ({ObserverX}, {ObserverY}) in bounds: {inBounds}");

            if (!inBounds)
            {
                Console.WriteLine("*** Observer is OUT OF BOUNDS! ***");
                return;
            }

            // Get observer terrain height
            float terrainHeight = (float)dem.GetElevation(ObserverX, ObserverY);
            Console.WriteLine($"  Terrain height at observer: {terrainHeight:F2}m");
            Console.WriteLine($"  Observer Z offset: {ObserverZ:F2}m");
            Console.WriteLine($"  Total observer height: {terrainHeight + ObserverZ:F2}m");

            // Get lat/lon
            var (lat, lon) = dem.Point2LatLonDeg(ObserverX, ObserverY);
            Console.WriteLine($"  Observer lat/lon: ({lat:F6}°, {lon:F6}°)");

            // Check nearby terrain at azimuth 51.75°
            double az = ProblemAzimuth * Math.PI / 180;
            
            Console.WriteLine($"\n--- Nearby terrain at azimuth {ProblemAzimuth}° ---");
            Console.WriteLine("Dist(px)  Px,Py           Elevation(m)  dH(m)");
            for (int distPx = 1; distPx <= 20; distPx++)
            {
                // Simple pixel-space approximation for nearby terrain
                double dx = distPx * Math.Sin(az);  // East component
                double dy = -distPx * Math.Cos(az); // North component (negative because Y increases southward)
                double px = ObserverX + dx;
                double py = ObserverY + dy;
                
                if (px >= 0 && px < dem.Width && py >= 0 && py < dem.Height)
                {
                    float h = (float)dem.GetElevation(px, py);
                    float dH = h - terrainHeight;
                    Console.WriteLine($"{distPx,8}  ({px:F1},{py:F1})  {h,12:F2}  {dH,8:F2}");
                }
            }
        }

        [TestMethod]
        public void DiagnoseNearFieldKernel()
        {
            // Specifically test the near-field ray casting behavior
            var demPath = DemPaths.FirstOrDefault(File.Exists);
            if (demPath == null)
            {
                Assert.Inconclusive("No DEMs found.");
            }

            var dem = new ElevationMap(demPath);
            var origin = new PixelOrigin { X = ObserverX, Y = ObserverY, Z = ObserverZ };

            Console.WriteLine("=== Near-Field Analysis ===");
            Console.WriteLine($"Testing first 50m at azimuth {ProblemAzimuth}°");

            // Use unified step mode to get regular 1.2m samples
            var csvPath = Path.Combine(OutputDir, "diag_nearfield_qt.csv");
            var qtResult = QuadTreeRayEmulator.Run(
                dem, origin, ProblemAzimuth, csvPath,
                suppressCsv: false, unifiedStepMode: true,
                logCoefficients: true, startDistanceMeters: 1.0);

            var refCsvPath = Path.Combine(OutputDir, "diag_nearfield_ref.csv");
            var refResult = ReferenceRayEmulator.Run(
                dem, origin, ProblemAzimuth, refCsvPath,
                suppressCsv: false, unifiedStepMode: true,
                maxDistanceMeters: 5000, startDistanceMeters: 1.0);

            // Compare near-field samples (first 50m = ~42 samples at 1.2m steps)
            Console.WriteLine("\n--- Near-Field Sample Comparison (1.2m steps) ---");
            Console.WriteLine("Step  Dist(m)   Ref_Elev   QT_Elev    Ref_Slope  QT_Slope   Elev_Diff");
            
            int maxSamples = Math.Min(50, Math.Min(refResult.Trace.Count, qtResult.Trace.Count));
            for (int i = 0; i < maxSamples; i++)
            {
                var r = refResult.Trace[i];
                var q = qtResult.Trace[i];
                double elevDiff = q.ElevationMeters - r.ElevationMeters;
                double slopeDiff = q.Slope - r.Slope;
                
                string flag = Math.Abs(slopeDiff) > 0.01 ? " ***" : "";
                Console.WriteLine($"{i,4}  {r.DistanceMeters,8:F1}  {r.ElevationMeters,9:F2}  {q.ElevationMeters,9:F2}  {r.Slope,10:F6}  {q.Slope,10:F6}  {elevDiff,9:F3}{flag}");
            }

            Console.WriteLine($"\nRef max slope: {refResult.Slopes.Max():F6} => {refResult.ElevationDeg:F4}°");
            Console.WriteLine($"QT max slope:  {qtResult.Slopes.Max():F6} => {qtResult.ElevationDeg:F4}°");
        }
    }
}
