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

// Configure Serilog
var exeDir = AppContext.BaseDirectory;
var logFilePath = Path.Combine(exeDir, "log.txt");
Log.Logger = new LoggerConfiguration()
    .MinimumLevel.Debug()
    //.WriteTo.File(logFilePath, rollingInterval: RollingInterval.Day)
    .WriteTo.Console(restrictedToMinimumLevel: Serilog.Events.LogEventLevel.Information)
    .CreateLogger();

Log.Debug("Application starting up");

Trace.Listeners.Clear();

MoonlibBridge.EnsureGdalInitialized();

Gdal.AllRegister();

var manager = SpiceManager.Singleton;       // Initialize

//var temp = Gdal.VersionInfo("BUILD_INFO");
//Log.Information("GDAL Version: {GdalVersion}", temp);
//Gdal.SetConfigOption("CPL_DEBUG", "ON");
//Gdal.SetConfigOption("GDAL_NUM_THREADS", "ALL_CPUS");

var stopwatch = Stopwatch.StartNew();

var dem_paths = new List<string>
{
    "/e/lunar_analyst_scenarios/haworth/dem.tif",
    "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
};

int runMode = 5;
if (args.Length > 0 && int.TryParse(args[0], out var parsedMode))
{
    runMode = parsedMode;
}
switch (runMode)
{
    case 0:
        //HorizonGenerator.GenerateHorizons("./output", dem_paths);
        //HorizonGenerator.GenerateHorizonsRayCasting(null, dem_paths);
        break;
    case 1:
        {
            var comparator = new HorizonComparator();
            comparator.CompareAndPlot("./output_comparison");
            break;
        }
    case 2:
        {
            bool enableNearFieldMerge = false;
            var mergeEnv = Environment.GetEnvironmentVariable("QUADTREE_NEARFIELD_MERGE");
            if (!string.IsNullOrEmpty(mergeEnv))
                enableNearFieldMerge = mergeEnv == "1" || mergeEnv.Equals("true", StringComparison.OrdinalIgnoreCase);

            float nearFieldMeters = 50f;
            var clampEnv = Environment.GetEnvironmentVariable("QUADTREE_NEARFIELD_METERS");
            if (!string.IsNullOrEmpty(clampEnv) && float.TryParse(clampEnv, NumberStyles.Float, CultureInfo.InvariantCulture, out var clampValue) && clampValue > 0f)
                nearFieldMeters = clampValue;

            using (var generator = new QuadTreeHorizonGenerator(disableHierarchy: false, enableNearFieldReferenceMerge: enableNearFieldMerge, nearFieldClampMeters: nearFieldMeters))
            {
                generator.GenerateHorizons("./output", dem_paths, 1024, 1024, 128, 128, 0f);
            }
            break;
        }
    case 3:
        horizon_runner.DebugSingleRay.Run();
        break;
    case 4:
        horizon_runner.DebugNearField.Run();
        break;
    case 5:
        {
            // Pipeline test: Generate horizons for all patches (or filtered subset)
            Console.WriteLine("Pipeline Mode: Generating horizons for patches");

            bool enableNearFieldMerge = false;
            var mergeEnv = Environment.GetEnvironmentVariable("QUADTREE_NEARFIELD_MERGE");
            if (!string.IsNullOrEmpty(mergeEnv))
                enableNearFieldMerge = mergeEnv == "1" || mergeEnv.Equals("true", StringComparison.OrdinalIgnoreCase);

            float nearFieldMeters = 250f;
            var clampEnv = Environment.GetEnvironmentVariable("QUADTREE_NEARFIELD_METERS");
            if (!string.IsNullOrEmpty(clampEnv) && float.TryParse(clampEnv, NumberStyles.Float, CultureInfo.InvariantCulture, out var clampValue) && clampValue > 0f)
                nearFieldMeters = clampValue;

            // Load DEMs
            var dem_paths2 = new List<string>
            {
                //"/e/lunar_analyst_scenarios/debug_scenario/dem.tif",
                "/e/lunar_analyst_scenarios/polar_mosaic/dem.tif",
                "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
            };
            var dems = dem_paths2.Select(path => new ElevationMap(path)).ToList();

            // Generate full patch list from primary DEM
            var allPatches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
            Console.WriteLine($"Total patches available: {allPatches.Count}");

            // Filter patches (adjust as needed)
            // Examples:
            // - First N patches: allPatches.Take(N)
            // - Specific range: allPatches.Skip(100).Take(50)
            // - Every 10th: allPatches.Where((p, i) => i % 10 == 0)
            // - Region: allPatches.Where(p => p.PatchX < 20 && p.PatchY < 20)

            int N = 4;  // Adjust this value
            var patchEnv = Environment.GetEnvironmentVariable("PIPELINE_PATCH_COUNT");
            if (!string.IsNullOrEmpty(patchEnv) && int.TryParse(patchEnv, out var patchCount) && patchCount > 0)
                N = patchCount;

            allPatches = QuadTreeHorizonGenerator.RemoveCompletedPatches(allPatches, @"/e/lunar_analyst_scenarios/polar_mosaic/lighting/horizons/", 0f);
            Console.WriteLine($"Processing {allPatches.Count} patches");

            // Create output directory
            string outputDir = "/e/lunar_analyst_scenarios/polar_mosaic/lighting/horizons/";
            Directory.CreateDirectory(outputDir);

            // Generate horizons for selected patches
            float observerElevation = 0f;
            using (var generator = new QuadTreeHorizonGenerator(
                disableHierarchy: false,
                enableNearFieldReferenceMerge: enableNearFieldMerge,
                nearFieldClampMeters: nearFieldMeters))
            {
                await generator.GenerateHorizonsForPatches(outputDir, dems, allPatches, observerElevation, compressHorizons: true);
            }

            Console.WriteLine($"Horizon files written to: {Path.GetFullPath(outputDir)}");
            break;
        }
    case 6:
        HorizonFile.CompressDirectory(@"/e/lunar_analyst_scenarios/haworth/lighting/horizons/", true, true);
        break;
    case 7:
        {
            var context = new AnalysisContext { DEM_path = dem_paths[0], HorizonDirectory = @"/e/lunar_analyst_scenarios/haworth/lighting/horizons/" };
            MapOperations.GeneratePermanentShadowMap(context, @"/e/lunar_analyst_scenarios/haworth/lighting/psr.tif").Wait();
        }
        break;
    case 8:
        {
            var dem_path = @"/e/lunar_analyst_scenarios/haworth/dem.tif";
            var horizon_dir = @"/e/lunar_analyst_scenarios/haworth/lighting/horizons/";
            var haven_dir = @"/e/lunar_analyst_scenarios/haworth/lighting/safe_havens/";
            Directory.CreateDirectory(haven_dir);
            var start_time = ViperDate.New(2027, 9, 1);
            var stop_time = ViperDate.New(2028, 3, 1);

            var context = new AnalysisContext { DEM_path = dem_path, HorizonDirectory = horizon_dir };
            MapOperations.GenerateSafeHavenDurations(context, haven_dir, start_time, stop_time).Wait();
        }
        break;
    case 9:
        {
            var dem_path = @"/e/lunar_analyst_scenarios/haworth/dem.tif";
            var horizon_dir = @"/e/lunar_analyst_scenarios/haworth/lighting/horizons/";
            var sun_dir = @"/e/lunar_analyst_scenarios/haworth/lighting/sun/";
            var camera_dir = @"/e/lunar_analyst_scenarios/haworth/lighting/camera/";

            Directory.CreateDirectory(sun_dir);
            Directory.CreateDirectory(camera_dir);

            var start_time = ViperDate.New(2027, 9, 1);
            var stop_time = ViperDate.New(2028, 3, 1);
            var timestamps = ViperDate.GetTimes(start_time, stop_time, TimeSpan.FromHours(2));
            var dem = new ElevationMap(dem_path);

            var pipeline = new LightmapPipeline();
            pipeline.ExecuteAsync(timestamps, sun_dir, camera_dir, dem, horizon_dir).Wait();
        }
        break;
    case 10:
        {
            // Pipeline test: Generate horizons for all patches (or filtered subset)
            Console.WriteLine("Pipeline Mode: Generating horizons for patches");

            bool enableNearFieldMerge = false;
            var mergeEnv = Environment.GetEnvironmentVariable("QUADTREE_NEARFIELD_MERGE");
            if (!string.IsNullOrEmpty(mergeEnv))
                enableNearFieldMerge = mergeEnv == "1" || mergeEnv.Equals("true", StringComparison.OrdinalIgnoreCase);

            float nearFieldMeters = 250f;
            var clampEnv = Environment.GetEnvironmentVariable("QUADTREE_NEARFIELD_METERS");
            if (!string.IsNullOrEmpty(clampEnv) && float.TryParse(clampEnv, NumberStyles.Float, CultureInfo.InvariantCulture, out var clampValue) && clampValue > 0f)
                nearFieldMeters = clampValue;

            // Load DEMs
            var dem_paths2 = new List<string>
            {
                "/e/lunar_analyst_scenarios/haworth/dem.tif",
                "/d/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
            };
            var dems = dem_paths2.Select(path => new ElevationMap(path)).ToList();

            // Generate full patch list from primary DEM
            var allPatches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
            Console.WriteLine($"Total patches: {allPatches.Count}");

            // Create output directory
            string outputDir = "/e/lunar_analyst_scenarios/haworth/lighting/horizons/";
            Directory.CreateDirectory(outputDir);

            allPatches = QuadTreeHorizonGenerator.RemoveCompletedPatches(allPatches, outputDir, 0f);
            Console.WriteLine($"Remaining patches: {allPatches.Count}");

            // Generate horizons for selected patches
            float observerElevation = 0f;
            using (var generator = new QuadTreeHorizonGenerator(
                disableHierarchy: false,
                enableNearFieldReferenceMerge: enableNearFieldMerge,
                nearFieldClampMeters: nearFieldMeters))
            {
                await generator.GenerateHorizonsForPatches(outputDir, dems, allPatches, observerElevation, compressHorizons: true);
            }

            Console.WriteLine($"Horizon files written to: {Path.GetFullPath(outputDir)}");
        }
        break;
    case 11:
        {
            var context = new AnalysisContext
            {
                DEM_path = @"/e/lunar_analyst_scenarios/haworth/dem.tif",
                HorizonDirectory = @"/e/lunar_analyst_scenarios/haworth/lighting/horizons/"
            };
            var filenames = new List<string> { @"/e/lunar_analyst_scenarios/haworth/landed_mission_durations.tif" };
            var time_step_hrs = 2f;
            var times = new List<List<DateTime>> { ViperDate.GetTimes(ViperDate.New(2027, 1, 1), ViperDate.New(2028, 3, 1), TimeSpan.FromHours(time_step_hrs)).ToList() };
            var reduce_lightcurve = MapOperations.MaxHoursOverThreshold(0.25f, time_step_hrs);
            MapOperations.GenerateLightingFunction(context, filenames, times, reduce_lightcurve).Wait();
        }
        break;
    default:
        throw new ArgumentOutOfRangeException();
}

var elapsed = stopwatch.Elapsed;
if (elapsed.TotalMinutes < 1.0)
    Console.WriteLine($"Time taken: {elapsed.TotalSeconds:F2} sec");
else
    Console.WriteLine($"Time taken: {elapsed.TotalMinutes:F2} min");
