using moonlib.horizon;
using moonlib.math;
using OSGeo.GDAL;
using Serilog;
using System.Globalization;

namespace moonlib_debugger;

internal static class Program
{
    private const int Width = 128;
    private const int Height = 128;
    private const int ObserverX = Width / 2;
    private const int ObserverY = Height / 2;
    private const float ObserverZ = 0f;

    private static int Main(string[] args)
    {
        ConfigureRuntime();

        var mode = args.FirstOrDefault()?.Trim().ToLowerInvariant() ?? "trace";
        var dem = CreateSyntheticStereoPeakDem();
        var dems = new List<ElevationMap> { dem };
        var origin = new PixelOrigin { X = ObserverX, Y = ObserverY, Z = ObserverZ };

        Console.WriteLine($"moonlib debugger mode: {mode}");
        Console.WriteLine($"DEM: {dem.Width}x{dem.Height}");
        Console.WriteLine($"Observer: ({origin.X}, {origin.Y}, {origin.Z:F1})");

        return mode switch
        {
            "trace" => RunTraceMode(dem, origin),
            "compare" => RunCompareMode(dems, origin),
            _ => PrintUsage()
        };
    }

    private static void ConfigureRuntime()
    {
        var baseDir = AppContext.BaseDirectory;
        var gdalData = ResolveGdalDataDir(baseDir);
        var projData = ResolveProjDataDir(baseDir);

        if (!string.IsNullOrWhiteSpace(gdalData))
        {
            Environment.SetEnvironmentVariable("GDAL_DATA", gdalData);
            Gdal.SetConfigOption("GDAL_DATA", gdalData);
        }

        if (!string.IsNullOrWhiteSpace(projData))
        {
            Environment.SetEnvironmentVariable("PROJ_LIB", projData);
            Environment.SetEnvironmentVariable("PROJ_DATA", projData);
            Gdal.SetConfigOption("PROJ_LIB", projData);
            Gdal.SetConfigOption("PROJ_DATA", projData);
        }

        Gdal.AllRegister();
        Log.Logger = new LoggerConfiguration()
            .MinimumLevel.Debug()
            .WriteTo.Console()
            .CreateLogger();
    }

    private static int RunTraceMode(ElevationMap dem, PixelOrigin origin)
    {
        const double azimuthDeg = 90.0;
        var tempDir = Path.Combine(Path.GetTempPath(), "moonlib_debugger");
        Directory.CreateDirectory(tempDir);

        Console.WriteLine($"Tracing azimuth {azimuthDeg:F2} deg");
        Console.WriteLine($"Scratch dir: {tempDir}");

        var refPath = Path.Combine(tempDir, "reference_trace.csv");
        var qtPath = Path.Combine(tempDir, "quadtree_trace.csv");

        var refResult = ReferenceRayEmulator.Run(dem, origin, azimuthDeg, refPath, suppressCsv: true, unifiedStepMode: true);
        var qtResult = QuadTreeRayEmulator.Run(dem, origin, azimuthDeg, qtPath, suppressCsv: true, unifiedStepMode: true);

        Console.WriteLine($"Reference samples: {refResult.Slopes.Length}");
        Console.WriteLine($"QuadTree samples:   {qtResult.Slopes.Length}");
        Console.WriteLine($"Reference max slope: {refResult.Slopes.DefaultIfEmpty(double.NaN).Max():F6}");
        Console.WriteLine($"QuadTree max slope:   {qtResult.Slopes.DefaultIfEmpty(double.NaN).Max():F6}");

        return 0;
    }

    private static int RunCompareMode(List<ElevationMap> dems, PixelOrigin origin)
    {
        var refGen = ReferenceHorizonGenerator.Singleton;
        using var qtGen = new QuadTreeHorizonGenerator(disableHierarchy: true, enableNearFieldReferenceMerge: false, nearFieldClampMeters: 50f);

        Console.WriteLine("Generating reference horizon...");
        var refHorizon = refGen.GenerateFromPixel(origin, dems);

        Console.WriteLine("Generating QuadTree horizon...");
        var qtHorizon = qtGen.GenerateHorizons(dems, (int)origin.X, (int)origin.Y, 1, 1, origin.Z);

        var refDeg = refHorizon.Elevations;
        var qtDeg = qtHorizon.Degrees;
        var maxDiff = 0.0f;
        var maxIdx = -1;

        for (var i = 0; i < Math.Min(refDeg.Length, qtDeg.Length); i++)
        {
            var diff = Math.Abs(refDeg[i] - qtDeg[i]);
            if (diff > maxDiff)
            {
                maxDiff = diff;
                maxIdx = i;
            }
        }

        Console.WriteLine($"Max diff: {maxDiff:F6} deg at azimuth bin {maxIdx}");
        if (maxIdx >= 0)
            Console.WriteLine($"Ref={refDeg[maxIdx]:F6} deg, QT={qtDeg[maxIdx]:F6} deg");

        return 0;
    }

    private static int PrintUsage()
    {
        Console.WriteLine("Usage:");
        Console.WriteLine("  moonlib_debugger trace   # compare one ray via the emulator path");
        Console.WriteLine("  moonlib_debugger compare # compare full horizons on a synthetic DEM");
        return 1;
    }

    private static ElevationMap CreateSyntheticStereoPeakDem()
    {
        var elevation = new float[Height, Width];
        elevation[ObserverY, ObserverX + 24] = 400f;

        const double pixelMeters = 30.0;
        var geoTransform = new double[] { 0.0, pixelMeters, 0.0, Height * pixelMeters, 0.0, -pixelMeters };
        const string stereoProj = "+proj=stere +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs";

        return new ElevationMap(elevation, stereoProj, geoTransform);
    }

    private static string? ResolveGdalDataDir(string baseDir)
    {
        var candidates = new[]
        {
            Path.Combine(baseDir, "gdal", "data"),
            Path.Combine(baseDir, "data"),
            Path.Combine(baseDir, "gdal-data"),
            Path.Combine(baseDir, "runtimes", "linux-x64", "native", "data"),
            Path.Combine(baseDir, "runtimes", "linux-x64", "native", "gdal-data"),
        };

        return candidates.FirstOrDefault(Directory.Exists);
    }

    private static string? ResolveProjDataDir(string baseDir)
    {
        if (File.Exists(Path.Combine(baseDir, "proj.db")))
            return baseDir;

        var candidates = new[]
        {
            Path.Combine(baseDir, "gdal", "share"),
            Path.Combine(baseDir, "share"),
            Path.Combine(baseDir, "proj-lib"),
            Path.Combine(baseDir, "runtimes", "linux-x64", "native", "share"),
            Path.Combine(baseDir, "runtimes", "linux-x64", "native", "proj-lib"),
        };

        return candidates.FirstOrDefault(path =>
            Directory.Exists(path) &&
            (File.Exists(Path.Combine(path, "proj.db")) || Directory.Exists(Path.Combine(path, "proj"))));
    }
}
