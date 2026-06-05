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

int runMode = 14;
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

            allPatches = QuadTreeHorizonGenerator.RemoveCompletedPatches(allPatches, @"/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons", 0f);
            Console.WriteLine($"Processing {allPatches.Count} patches");

            // Create output directory
            string outputDir = "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons";
            Directory.CreateDirectory(outputDir);

            // Generate horizons for selected patches
            float observerElevation = 0f;
            using (var generator = new QuadTreeHorizonGenerator(
                disableHierarchy: false))
            {
                await generator.GenerateHorizonsForPatches(outputDir, dems, allPatches, observerElevation, compressHorizons: false);
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
            var horizon_filenames = new List<string>
            {
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/03328/horizon_03328_06528_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/03328/horizon_03328_10624_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/03328/horizon_03328_14720_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/03328/horizon_03328_18816_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/03328/horizon_03328_22912_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/03328/horizon_03328_27008_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/03968/horizon_03968_36096_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/04096/horizon_04096_00256_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/04096/horizon_04096_04352_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/04096/horizon_04096_08448_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/04096/horizon_04096_12544_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/04096/horizon_04096_16640_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/04096/horizon_04096_20736_000.bin",
                "/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/04096/horizon_04096_24832_000.bin",

            };
            var context = new AnalysisContext
            {
                DEM_path = @"/e/lunar_analyst_scenarios/polar_mosaic/dem.tif",
                HorizonDirectory = @"/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/horizons/"
            };
            MapOperations.GeneratePermanentShadowMap(context,
                @"/media/mhs/BEB8-5B41/new_datasets/polar_mosaic/psr.tif",
                horizon_filenames: horizon_filenames).Wait();
        }
        break;
    case 9:
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
    case 10:
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
    case 11:
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
    case 12:
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
    case 13:
        {
            var context = new AnalysisContext
            {
                DEM_path = @"/workspace/polar_mosaic/dems/dem.tif",
                HorizonDirectory = @"/workspace/polar_mosaic/horizons/"
            };
            var filenames = new List<string> { @"/workspace/polar_mosaic/max_landed_mission_duration_2027_2032.tif" };
            var time_step_hrs = 6f;
            var times = new List<List<DateTime>> { ViperDate.GetTimes(ViperDate.New(2027, 1, 1), ViperDate.New(2032, 1, 1), TimeSpan.FromHours(time_step_hrs)).ToList() };
            var reduce_lightcurve = MapOperations.MaxHoursOverThreshold(0.5f, time_step_hrs);
            MapOperations.GenerateLightingFunction(context, filenames, times, reduce_lightcurve).Wait();
        }
        break;
    case 14:
        {

            var DEM_path = @"/e/lunar_analyst_scenarios/haworth/dem.tif";
            var HorizonDirectory = @"/e/lunar_analyst_scenarios/haworth/lighting/horizons/";

            //var DEM_path = @"/e/lunar_analyst_scenarios/polar_mosaic/dem.tif";
            //var HorizonDirectory = @"/e/lunar_analyst_scenarios/polar_mosaic/horizons/";

            var time_step_hrs = 6f;
            var times = ViperDate.GetTimes(ViperDate.New(2027, 1, 1), ViperDate.New(2027, 2, 1), TimeSpan.FromHours(time_step_hrs)).ToList();
            
            var count = 0;
            var lm = new Lightmaps(4);
            //foreach (var r in lm.StreamElevationPatches(DEM_path, times))
            //    Console.WriteLine($"Generated lightmap patch {++count}");
            Console.WriteLine("[Main] Calling StreamElevationOverTerrainPatches...");
            Console.Out.Flush();
            var queue = lm.StreamElevationOverTerrainPatches(DEM_path, HorizonDirectory, times);
            foreach (var r in queue.GetConsumingEnumerable())
            {
                Console.WriteLine($"Generated lightmap patch {++count}");
                Console.Out.Flush();
            }
            Console.WriteLine("[Main] Foreach completed.");
            Console.Out.Flush();
            if (lm.BackgroundTaskError is not null)
            {
                Console.WriteLine($"FATAL BACKGROUND ERROR: {lm.BackgroundTaskError}");
                Console.Out.Flush();
                throw new Exception("Background task failed", lm.BackgroundTaskError);
            }
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
