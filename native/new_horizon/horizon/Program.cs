using moonlib;
using moonlib.horizon;
using moonlib.mapops;
using moonlib.spice;
using OSGeo.GDAL;
using Serilog;
using System.Diagnostics;
using System.Globalization;
using moonlib.pipeline.streaming;
using moonlib.util;
using moonlib.pipeline;

internal static class Program
{
    private sealed record MakeOptions(
        string HorizonsDirectory,
        int FirstHorizonIndex,
        int HorizonCount,
        IReadOnlyList<string> DemPaths,
        float ObserverElevationMeters);

    private sealed record PsrOptions(
        string HorizonsDirectory,
        string DemPath,
        string OutputPath);

    private static async Task<int> Main(string[] args)
    {
        ConfigureLogging();
        Log.Debug("Application starting up");

        try
        {
            if (args.Length == 0)
            {
                Log.Error("A verb is required.");
                PrintUsage();
                return 1;
            }

            MoonlibBridge.EnsureGdalInitialized();
            Gdal.AllRegister();
            _ = SpiceManager.Singleton;

            var verb = args[0].Trim().ToLowerInvariant();
            return verb switch
            {
                "make" => await RunMakeAsync(args),
                "psr" => await RunPsrAsync(args),
                _ => UnknownVerb(verb),
            };
        }
        catch (Exception ex)
        {
            Log.Error(ex, "Unhandled exception");
            return 1;
        }
        finally
        {
            Log.CloseAndFlush();
        }
    }

    private static int UnknownVerb(string verb)
    {
        Log.Error("Unknown verb: {Verb}", verb);
        PrintUsage();
        return 1;
    }

    private static async Task<int> RunMakeAsync(string[] args)
    {
        var options = ParseMakeArgs(args);
        if (options is null)
        {
            return 1;
        }

        var dems = options.DemPaths.Select(path => new ElevationMap(path)).ToList();

        var allPatches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
        var selectedPatches = allPatches
            .Skip(options.FirstHorizonIndex)
            .Take(options.HorizonCount)
            .ToList();

        selectedPatches = QuadTreeHorizonGenerator.RemoveCompletedPatches(
            selectedPatches,
            options.HorizonsDirectory,
            options.ObserverElevationMeters);

	if (selectedPatches.Count < 1)
	{
	    Log.Information("There are no patches that need to be processed", selectedPatches.Count);
	    return 0;
	}

        Log.Information("Processing {PatchCount} patches", selectedPatches.Count);

        using (var generator = new QuadTreeHorizonGenerator(disableHierarchy: false))
        {
            await generator.GenerateHorizonsForPatches(
                options.HorizonsDirectory,
                dems,
                selectedPatches,
                options.ObserverElevationMeters,
                compressHorizons: true);
        }

        Log.Information("Horizon files written to: {OutputDir}", Path.GetFullPath(options.HorizonsDirectory));
        return 0;
    }

    private static async Task<int> RunPsrAsync(string[] args)
    {
        var options = ParsePsrArgs(args);
        if (options is null)
        {
            return 1;
        }

        var context = new AnalysisContext
        {
            DEM_path = options.DemPath,
            HorizonDirectory = options.HorizonsDirectory,
        };

        var outputDir = Path.GetDirectoryName(options.OutputPath);
        if (!string.IsNullOrWhiteSpace(outputDir))
        {
            Directory.CreateDirectory(outputDir);
        }

        await MapOperations.GeneratePermanentShadowMap(context, options.OutputPath);
        Log.Information("PSR file written to: {OutputPath}", Path.GetFullPath(options.OutputPath));
        return 0;
    }

    private static MakeOptions? ParseMakeArgs(string[] args)
    {
        if (args.Length < 5)
        {
            Log.Error("Expected at least 5 arguments for 'make'.");
            PrintUsage();
            return null;
        }

        var horizonsDirectory = args[1];
        if (!Directory.Exists(horizonsDirectory))
        {
            Log.Error("Horizon target directory must already exist: {HorizonsDirectory}", horizonsDirectory);
            return null;
        }

        if (!int.TryParse(args[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out var firstHorizonIndex))
        {
            Log.Error("Horizon first index must be an integer: {Value}", args[2]);
            return null;
        }

        if (!int.TryParse(args[3], NumberStyles.Integer, CultureInfo.InvariantCulture, out var horizonCount))
        {
            Log.Error("Horizon count must be an integer: {Value}", args[3]);
            return null;
        }

        if (firstHorizonIndex < 0)
        {
            Log.Error("Horizon first index must be >= 0: {Value}", firstHorizonIndex);
            return null;
        }

        if (horizonCount <= 0)
        {
            Log.Error("Horizon count must be > 0: {Value}", horizonCount);
            return null;
        }

        var demPaths = args.Skip(4).ToList();
        if (demPaths.Count == 0)
        {
            Log.Error("At least one DEM file path is required for 'make'.");
            PrintUsage();
            return null;
        }

        var missingDemPaths = demPaths.Where(path => !File.Exists(path)).ToList();
        if (missingDemPaths.Count > 0)
        {
            foreach (var missingDemPath in missingDemPaths)
            {
                Log.Error("DEM file does not exist: {DemPath}", missingDemPath);
            }
            return null;
        }

        const float observerElevationMeters = 0f;
        return new MakeOptions(
            horizonsDirectory,
            firstHorizonIndex,
            horizonCount,
            demPaths,
            observerElevationMeters);
    }

    private static PsrOptions? ParsePsrArgs(string[] args)
    {
        if (args.Length != 4)
        {
            Log.Error("Expected exactly 4 arguments for 'psr'.");
            PrintUsage();
            return null;
        }

        var horizonsDirectory = args[1];
        if (!Directory.Exists(horizonsDirectory))
        {
            Log.Error("Horizon directory must already exist: {HorizonsDirectory}", horizonsDirectory);
            return null;
        }

        var demPath = args[2];
        if (!File.Exists(demPath))
        {
            Log.Error("DEM file does not exist: {DemPath}", demPath);
            return null;
        }

        var outputPath = args[3];
        if (string.IsNullOrWhiteSpace(outputPath))
        {
            Log.Error("Output path for 'psr' must be provided.");
            return null;
        }

        return new PsrOptions(horizonsDirectory, demPath, outputPath);
    }

    private static void PrintUsage()
    {
        Console.WriteLine("Usage:");
        Console.WriteLine("  horizon make <horizons_directory> <first> <count> <dem_filenames ...>");
        Console.WriteLine("  horizon psr <horizons_directory> <dem_filename> <output_tiff>");
        Console.WriteLine();
        Console.WriteLine("Examples:");
        Console.WriteLine("  horizon make /workspace/scenario/horizons 0 100 /workspace/scenario/dems/primary.tif");
        Console.WriteLine("  horizon psr /workspace/scenario/horizons /workspace/scenario/dems/primary.tif /workspace/scenario/lighting/psr.tif");
    }

    private static void ConfigureLogging()
    {
        Log.Logger = new LoggerConfiguration()
            .MinimumLevel.Debug()
            .WriteTo.Console(restrictedToMinimumLevel: Serilog.Events.LogEventLevel.Information)
            .CreateLogger();

        Trace.Listeners.Clear();
    }
}
