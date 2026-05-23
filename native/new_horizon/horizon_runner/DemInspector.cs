using System;
using System.Globalization;
using OSGeo.OSR; // For SpatialReference
using moonlib.math;
using moonlib.horizon;

namespace horizon_runner
{
    public class DemInspector
    {
        public static void Inspect(string demPath, PixelOrigin origin)
        {
            Console.WriteLine($"\n--- Inspecting DEM: {demPath} ---");
            var dem = new ElevationMap(demPath);

            Console.WriteLine($"DEM Width: {dem.Width}, Height: {dem.Height}");
            Console.WriteLine($"DEM Projection: {dem.Projection}");
            Console.WriteLine($"DEM Proj4: {dem.Proj4}");

            Console.WriteLine("\n--- GeoTransform ---");
            for (int i = 0; i < dem.GeoTransform.Length; i++)
            {
                Console.WriteLine($"GeoTransform[{i}]: {dem.GeoTransform[i].ToString(CultureInfo.InvariantCulture)}");
            }

            Console.WriteLine("\n--- SRS Descriptor ---");
            Console.WriteLine($"Type: {dem.SrsDescriptor.Type}");
            Console.WriteLine($"R (Radius): {dem.SrsDescriptor.R.ToString(CultureInfo.InvariantCulture)}");
            Console.WriteLine($"Lat0 (Origin Latitude): {dem.SrsDescriptor.lat0.ToDegrees().ToString(CultureInfo.InvariantCulture)} deg ({dem.SrsDescriptor.lat0.ToString(CultureInfo.InvariantCulture)} rad)");
            Console.WriteLine($"Lon0 (Origin Longitude): {dem.SrsDescriptor.lon0.ToDegrees().ToString(CultureInfo.InvariantCulture)} deg ({dem.SrsDescriptor.lon0.ToString(CultureInfo.InvariantCulture)} rad)");
            Console.WriteLine($"K0 (Scale Factor): {dem.SrsDescriptor.k0.ToString(CultureInfo.InvariantCulture)}");
            Console.WriteLine($"False Easting: {dem.SrsDescriptor.FalseEasting.ToString(CultureInfo.InvariantCulture)}");
            Console.WriteLine($"False Northing: {dem.SrsDescriptor.FalseNorthing.ToString(CultureInfo.InvariantCulture)}");

            // Calculate observer Lat/Lon
            var (obs_lat_deg, obs_lon_deg) = dem.Point2LatLonDeg((double)origin.X, (double)origin.Y);
            Console.WriteLine($"\nObserver (pixel {origin.X},{origin.Y}) -> Lat: {obs_lat_deg.ToString(CultureInfo.InvariantCulture)} deg, Lon: {obs_lon_deg.ToString(CultureInfo.InvariantCulture)} deg");

            // Calculate Grid Convergence at observer point
            try
            {
                var obsLonRad = obs_lon_deg.ToRadians();
                var obsLatRad = obs_lat_deg.ToRadians();
                var obsLonLatPoint = new CRSPoint(obsLonRad, obsLatRad);
                var (k_distortion, gamma_rad) = MoonSrsLambdaFactory.GetDistortion(obsLonLatPoint, dem.SrsDescriptor);
                Console.WriteLine($"\nGrid Convergence (gamma) at observer: {gamma_rad.ToDegrees().ToString(CultureInfo.InvariantCulture)} deg ({gamma_rad.ToString(CultureInfo.InvariantCulture)} rad)");
                Console.WriteLine($"Projection Scale Factor (k) at observer: {k_distortion.ToString(CultureInfo.InvariantCulture)}");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Could not calculate Grid Convergence: {ex.Message}");
            }
        }
    }
}