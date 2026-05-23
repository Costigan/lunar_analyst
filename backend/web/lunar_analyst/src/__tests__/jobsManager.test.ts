import { describe, expect, it } from "vitest";
import type { ToolDefinition } from "../services/toolService";
import {
  applyJobEventToRun,
  buildParameterRows,
  buildSnapshotRows,
  buildJobTemplate,
  buildLaunchPayload,
  createRunRecord,
  normalizePercent,
  normalizeRunStatus,
  orderJobDefinitions,
  runStatusFromEventName,
  upsertRecentRuns,
  validateDraftParams,
} from "../utils/jobsManager";

describe("jobsManager utils", () => {
  it("orders notebook jobs ahead of system jobs", () => {
    const defs: ToolDefinition[] = [
      { job_definition_id: "system:one", title: "Sys One", route_path: "/sys", job_type: "system", params: [] },
      { job_definition_id: "notebook:two", title: "NB Two", route_path: "/nb", job_type: "notebook", params: [] },
    ];
    expect(orderJobDefinitions(defs).map((d) => d.job_definition_id)).toEqual([
      "notebook:two",
      "system:one",
    ]);
  });

  it("builds notebook template with scenario id", () => {
    const def: ToolDefinition = {
      job_definition_id: "notebook:terrain",
      title: "Terrain",
      route_path: "/api/v1/jobs/notebook",
      job_type: "notebook",
      params: [],
    };
    expect(buildJobTemplate(def, "scenario-a")).toEqual({
      scenario_id: "scenario-a",
      notebook_job_id: "terrain",
      params: {},
      runtime_mode: "osgeo",
    });
  });

  it("defaults notebook generate_horizons job to compressed output", () => {
    const def: ToolDefinition = {
      job_definition_id: "notebook:script-generate_horizons",
      title: "Generate Horizons",
      route_path: "/api/v1/jobs/run-notebook-definition",
      job_type: "notebook",
      params: [],
    };
    expect(buildJobTemplate(def, "scenario-a")).toEqual({
      scenario_id: "scenario-a",
      notebook_job_id: "script-generate_horizons",
      params: { compress_horizons: true },
      runtime_mode: "osgeo",
    });
  });

  it("builds launch payload for single-param and multi-param jobs", () => {
    const singleDef: ToolDefinition = {
      job_definition_id: "system:single",
      title: "Single",
      route_path: "/single",
      job_type: "system",
      params: [{ name: "request", type: "object" }],
    };
    const multiDef: ToolDefinition = {
      job_definition_id: "system:multi",
      title: "Multi",
      route_path: "/multi",
      job_type: "system",
      params: [
        { name: "scenario_id", type: "string" },
        { name: "limit", type: "int" },
      ],
    };

    expect(buildLaunchPayload(singleDef, { request: { a: 1 } })).toEqual({ a: 1 });
    expect(buildLaunchPayload(multiDef, { scenario_id: "s", limit: 1 })).toEqual({
      scenario_id: "s",
      limit: 1,
    });
  });

  it("preserves explicit null defaults in system templates", () => {
    const def: ToolDefinition = {
      job_definition_id: "system:psr",
      title: "Generate PSR",
      route_path: "/api/v1/jobs/generate-psr-raster",
      job_type: "system",
      params: [
        { name: "scenario_id", type: "str" },
        { name: "dem_path", type: "str | None", default: null },
      ],
    };
    expect(buildJobTemplate(def, "scn_test")).toEqual({
      scenario_id: "scn_test",
      dem_path: null,
    });
  });

  it("fills scenario-rooted defaults for common system path params", () => {
    const def: ToolDefinition = {
      job_definition_id: "native:generate_psr_raster",
      handler_name: "generate_psr_raster",
      title: "Generate PSR Raster",
      route_path: "/api/v1/jobs/generate-psr-raster",
      job_type: "system",
      params: [
        { name: "scenario_id", type: "str" },
        { name: "dem_path", type: "str" },
        { name: "horizons_dir", type: "str" },
        { name: "output_path", type: "str" },
        { name: "scenario_root_dir", type: "str | None", default: null },
      ],
    };
    expect(
      buildJobTemplate(
        def,
        "scn_test",
        {
          scenarioRootDir: "/e/lunar_analyst_scenarios/test_scenario",
          demPath: "/e/lunar_analyst_scenarios/test_scenario/dem.tif",
        },
      ),
    ).toEqual({
      scenario_id: "scn_test",
      dem_path: "/e/lunar_analyst_scenarios/test_scenario/dem.tif",
      horizons_dir: "/e/lunar_analyst_scenarios/test_scenario/lighting/horizons",
      output_path: "/e/lunar_analyst_scenarios/test_scenario/lighting/psr.tif",
      scenario_root_dir: "/e/lunar_analyst_scenarios/test_scenario",
    });
  });

  it("normalizes progress percentages and status values", () => {
    expect(normalizePercent(-5)).toBe(0);
    expect(normalizePercent("91.337")).toBe(91.3);
    expect(normalizePercent(125)).toBe(100);
    expect(normalizePercent("abc")).toBeNull();
    expect(normalizeRunStatus("RUNNING")).toBe("running");
    expect(normalizeRunStatus("weird", "queued")).toBe("queued");
    expect(runStatusFromEventName("job_failed")).toBe("failed");
  });

  it("updates run records with progress and terminal events", () => {
    const run = createRunRecord({
      runId: "job-1",
      scenarioId: "scn-1",
      definitionId: "native:test",
      title: "Test Job",
      status: "queued",
      paramsSnapshot: { scenario_id: "scn-1" },
      nowMs: 1000,
    });
    const started = applyJobEventToRun(run, "job_started", { job_id: "job-1" }, 2000);
    const progressed = applyJobEventToRun(started, "job_progress", { job_id: "job-1", percent: 42.2, message: "Working" }, 3000);
    const completed = applyJobEventToRun(progressed, "job_completed", { job_id: "job-1", result: { status: "ok" } }, 5000);

    expect(started.status).toBe("running");
    expect(progressed.percent).toBe(42.2);
    expect(progressed.latestMessage).toBe("Working");
    expect(completed.status).toBe("completed");
    expect(completed.percent).toBe(100);
    expect(completed.finishedAtMs).toBe(5000);
    expect(completed.messages[0]?.eventName).toBe("job_completed");
    expect(completed.resultSummary).toContain("\"status\":\"ok\"");
  });

  it("updates run records with cancellation events", () => {
    const run = createRunRecord({
      runId: "job-cancel",
      scenarioId: "scn-1",
      definitionId: "native:generate_horizons",
      title: "Generate Horizons",
      status: "running",
      paramsSnapshot: { scenario_id: "scn-1" },
      nowMs: 1000,
    });

    const cancelled = applyJobEventToRun(
      run,
      "job_cancelled",
      { job_id: "job-cancel", reason: "cancel requested" },
      3000,
    );

    expect(cancelled.status).toBe("cancelled");
    expect(cancelled.finishedAtMs).toBe(3000);
    expect(cancelled.latestMessage).toBe("Run cancelled.");
    expect(cancelled.messages[0]?.eventName).toBe("job_cancelled");
  });

  it("shows structured horizon patch progress from worker events", () => {
    const run = createRunRecord({
      runId: "job-horizons",
      scenarioId: "scn-1",
      definitionId: "native:generate_horizons",
      title: "Generate Horizons",
      status: "running",
      paramsSnapshot: { scenario_id: "scn-1" },
      nowMs: 1000,
    });

    const progressed = applyJobEventToRun(
      run,
      "job_progress",
      {
        job_id: "job-horizons",
        stage: "process_patches",
        message: "Generated 123/292 horizon patches.",
        percent: 42.1,
        processed: 123,
        total: 292,
        file_name: "horizon_00128_00256_000.cbin",
      },
      2000,
    );

    expect(progressed.status).toBe("running");
    expect(progressed.percent).toBe(42.1);
    expect(progressed.latestMessage).toBe("Generated 123/292 horizon patches.");
    expect(progressed.messages[0]?.raw.stage).toBe("process_patches");
    expect(progressed.messages[0]?.raw.processed).toBe(123);
    expect(progressed.messages[0]?.raw.total).toBe(292);
  });

  it("keeps recent runs sorted and capped", () => {
    const runA = createRunRecord({
      runId: "a",
      scenarioId: "s",
      definitionId: null,
      title: "A",
      status: "running",
      paramsSnapshot: {},
      nowMs: 1000,
    });
    const runB = createRunRecord({
      runId: "b",
      scenarioId: "s",
      definitionId: null,
      title: "B",
      status: "running",
      paramsSnapshot: {},
      nowMs: 2000,
    });
    const runC = createRunRecord({
      runId: "c",
      scenarioId: "s",
      definitionId: null,
      title: "C",
      status: "running",
      paramsSnapshot: {},
      nowMs: 3000,
    });
    let runs = upsertRecentRuns([], runA, 2);
    runs = upsertRecentRuns(runs, runB, 2);
    runs = upsertRecentRuns(runs, runC, 2);
    expect(runs.map((item) => item.runId)).toEqual(["c", "b"]);
  });

  it("builds table rows for notebook params and validates required fields", () => {
    const definition: ToolDefinition = {
      job_definition_id: "notebook:my_job",
      title: "Notebook Job",
      route_path: "/api/v1/jobs/notebook",
      job_type: "notebook",
      params: [],
    };
    const draft = {
      scenario_id: "scn-7",
      notebook_job_id: "my_job",
      params: { compress_horizons: true, tile_size: 128 },
    };
    const rows = buildParameterRows(definition, draft);
    expect(rows.map((row) => row.name)).toContain("params.compress_horizons");
    expect(rows.map((row) => row.name)).toContain("params.tile_size");
    expect(validateDraftParams(definition, draft)).toHaveLength(0);
    expect(validateDraftParams(definition, { ...draft, scenario_id: "" })).toContain(
      "Parameter \"scenario_id\" is required.",
    );
  });

  it("flattens snapshot rows for nested values", () => {
    const rows = buildSnapshotRows({
      scenario_id: "scn",
      params: {
        compress_horizons: true,
        tiles: [1, 2],
      },
    });
    expect(rows.map((row) => row.key)).toContain("params.compress_horizons");
    expect(rows.map((row) => row.key)).toContain("params.tiles[0]");
  });
});
