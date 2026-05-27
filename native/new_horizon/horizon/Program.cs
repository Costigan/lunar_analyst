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
    private const int DefaultGpuConcurrency = QuadTreeHorizonGenerator.DefaultMaxConcurrentGpuOps;
    private const int DefaultSegmentQueueSize = QuadTreeHorizonGenerator.DefaultSegmentQueueSize;

    private sealed record MakeOptions(
        string HorizonsDirectory,
        int PatchOffset,
        int PatchStride,
        IReadOnlyList<string> DemPaths,
        int GpuConcurrency,
        int SegmentQueue,
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
            .Where((_, index) => index % options.PatchStride == options.PatchOffset)
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

        using (var generator = new QuadTreeHorizonGenerator(
            disableHierarchy: false,
            maxConcurrentGpuOps: options.GpuConcurrency,
            maxSegmentQueueSize: options.SegmentQueue))
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

        if (!int.TryParse(args[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out var patchOffset))
        {
            Log.Error("Patch offset must be an integer: {Value}", args[2]);
            return null;
        }

        if (!int.TryParse(args[3], NumberStyles.Integer, CultureInfo.InvariantCulture, out var patchStride))
        {
            Log.Error("Patch stride must be an integer: {Value}", args[3]);
            return null;
        }

        if (patchOffset < 0)
        {
            Log.Error("Patch offset must be >= 0: {Value}", patchOffset);
            return null;
        }

        if (patchStride <= 0)
        {
            Log.Error("Patch stride must be > 0: {Value}", patchStride);
            return null;
        }

        if (patchOffset >= patchStride)
        {
            Log.Error("Patch offset must be less than patch stride: offset={PatchOffset}, stride={PatchStride}", patchOffset, patchStride);
            return null;
        }

        var demPaths = new List<string>();
        var gpuConcurrency = DefaultGpuConcurrency;
        var segmentQueueSize = DefaultSegmentQueueSize;
        for (int i = 4; i < args.Length; i++)
        {
            var arg = args[i];
            if (arg == "--gpu-concurrency")
            {
                if (i + 1 >= args.Length)
                {
                    Log.Error("--gpu-concurrency requires an integer value.");
                    return null;
                }

                i++;
                if (!TryParseGpuConcurrency(args[i], out gpuConcurrency))
                    return null;
                continue;
            }

            if (arg == "--segment-queue")
            {
                if (i + 1 >= args.Length)
                {
                    Log.Error("--segment-queue requires an integer value.");
                    return null;
                }

                i++;
                if (!TryParseSegmentQueue(args[i], out segmentQueueSize))
                    return null;
                continue;
            }

            const string gpuConcurrencyPrefix = "--gpu-concurrency=";
            if (arg.StartsWith(gpuConcurrencyPrefix, StringComparison.Ordinal))
            {
                var value = arg[gpuConcurrencyPrefix.Length..];
                if (!TryParseGpuConcurrency(value, out gpuConcurrency))
                    return null;
                continue;
            }

            const string segmentQueuePrefix = "--segment-queue=";
            if (arg.StartsWith(segmentQueuePrefix, StringComparison.Ordinal))
            {
                var value = arg[segmentQueuePrefix.Length..];
                if (!TryParseSegmentQueue(value, out segmentQueueSize))
                    return null;
                continue;
            }

            demPaths.Add(arg);
        }

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
            patchOffset,
            patchStride,
            demPaths,
            gpuConcurrency,
            segmentQueueSize,
            observerElevationMeters);
    }

    private static bool TryParseGpuConcurrency(string value, out int gpuConcurrency)
    {
        if (!int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out gpuConcurrency))
        {
            Log.Error("GPU concurrency must be an integer: {Value}", value);
            return false;
        }

        if (gpuConcurrency <= 0)
        {
            Log.Error("GPU concurrency must be > 0: {Value}", gpuConcurrency);
            return false;
        }

        return true;
    }

    private static bool TryParseSegmentQueue(string value, out int result)
    {
        if (!int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out result))
        {
            Log.Error("segment-queue size must be an integer: {Value}", value);
            return false;
        }

        if (result <= 0)
        {
            Log.Error("segment-queue size must be > 0: {Value}", result);
            return false;
        }

        return true;
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
        Console.WriteLine("  horizon make <horizons_directory> <offset> <stride> [--gpu-concurrency <count>] [--segment-queue <size>] <dem_filenames ...>");
        Console.WriteLine("  horizon psr <horizons_directory> <dem_filename> <output_tiff>");
        Console.WriteLine();
        Console.WriteLine("Examples:");
        Console.WriteLine("  horizon make /workspace/scenario/horizons 0 16 --gpu-concurrency 4 --segment-queue 6 /workspace/scenario/dems/primary.tif");
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
