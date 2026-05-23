import type { ToolDefinition } from "../services/toolService";

export function orderJobDefinitions(definitions: ToolDefinition[]): ToolDefinition[] {
  const notebook = definitions.filter((definition) => definition.job_type === "notebook");
  const system = definitions.filter((definition) => definition.job_type !== "notebook");
  return [...notebook, ...system];
}

export type JobTemplateContext = {
  scenarioRootDir: string | null;
  demPath: string | null;
};

export type JobRunStatus = "idle" | "queued" | "running" | "completed" | "failed" | "cancelled";

export type JobMessageLevel = "progress" | "info" | "warn" | "error" | "system";

export type JobRunMessage = {
  id: string;
  timestampMs: number;
  eventName: string;
  level: JobMessageLevel;
  text: string;
  raw: Record<string, unknown>;
};

export type JobRunRecord = {
  runId: string;
  scenarioId: string | null;
  definitionId: string | null;
  title: string;
  status: JobRunStatus;
  percent: number | null;
  latestMessage: string;
  requestedAtMs: number | null;
  startedAtMs: number | null;
  finishedAtMs: number | null;
  updatedAtMs: number;
  paramsSnapshot: Record<string, unknown>;
  messages: JobRunMessage[];
  resultSummary: string | null;
};

export type ParameterRow = {
  key: string;
  path: string[];
  name: string;
  value: unknown;
  typeHint: string;
  required: boolean;
  nullable: boolean;
  readOnly: boolean;
  note: string;
};

export type SnapshotRow = {
  key: string;
  valueText: string;
  typeHint: string;
};

const TERMINAL_STATUSES: Set<JobRunStatus> = new Set(["completed", "failed", "cancelled"]);

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function deepCloneValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => deepCloneValue(item));
  if (isPlainRecord(value)) {
    const cloned: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      cloned[key] = deepCloneValue(item);
    }
    return cloned;
  }
  return value;
}

export function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  return deepCloneValue(value) as Record<string, unknown>;
}

function normalizePath(path: string | null | undefined): string {
  return String(path || "").trim().replace(/\\/g, "/");
}

function joinPath(base: string, ...parts: string[]): string {
  const normalizedBase = normalizePath(base).replace(/\/+$/g, "");
  if (!normalizedBase) return "";
  const cleaned = parts.map((part) => String(part || "").replace(/^\/+|\/+$/g, "")).filter(Boolean);
  return cleaned.length === 0 ? normalizedBase : `${normalizedBase}/${cleaned.join("/")}`;
}

function defaultOutputPath(definition: ToolDefinition, context: JobTemplateContext): string {
  const root = normalizePath(context.scenarioRootDir);
  if (!root) return "";
  const implementation = String(
    definition.implementation_name || definition.handler_name || definition.job_definition_id || "",
  ).toLowerCase();
  if (implementation.includes("generate_psr_raster")) return joinPath(root, "lighting", "psr.tif");
  if (implementation.includes("generate_average_sun_fraction_raster")) {
    return joinPath(root, "lighting", "average_sun_fraction.tif");
  }
  if (implementation.includes("generate_earth_above_terrain_duration_raster")) {
    return joinPath(root, "lighting", "earth_above_terrain_duration.tif");
  }
  if (implementation.includes("generate_combined_sun_earth_max_contiguous_duration_raster")) {
    return joinPath(root, "lighting", "combined_sun_earth_max_contiguous_duration.tif");
  }
  const suffix = implementation
    .replace(/^native:/, "")
    .replace(/[^a-z0-9_.-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return joinPath(root, "outputs", `${suffix || "job_output"}.tif`);
}

function sampleValue(typeName: string, paramName: string, activeScenarioId: string | null): unknown {
  const t = String(typeName || "").toLowerCase();
  if (t.includes("dict") || t.includes("object") || t.includes("json")) return {};
  if (t.includes("int") || t.includes("float")) return 0;
  if (t.includes("bool")) return false;
  if (paramName === "scenario_id") return activeScenarioId || "";
  return "";
}

function parseNotebookJobId(jobDefinitionId: string): string {
  return jobDefinitionId.startsWith("notebook:") ? jobDefinitionId.slice("notebook:".length) : jobDefinitionId;
}

export function buildJobTemplate(
  definition: ToolDefinition,
  activeScenarioId: string | null,
  context: JobTemplateContext = { scenarioRootDir: null, demPath: null },
): Record<string, unknown> {
  if (definition.job_type === "notebook") {
    const notebookJobId = parseNotebookJobId(definition.job_definition_id);
    const params: Record<string, unknown> = {};
    if (notebookJobId === "generate_horizons" || notebookJobId === "script-generate_horizons") {
      params.compress_horizons = true;
    }
    let runtimeMode: string = "osgeo";
    for (const param of definition.params || []) {
      if (param.name === "runtime_mode" && typeof param.default === "string" && param.default.trim()) {
        runtimeMode = param.default.trim();
      }
    }
    return {
      scenario_id: activeScenarioId || "",
      notebook_job_id: notebookJobId,
      params,
      runtime_mode: runtimeMode,
    };
  }
  const template: Record<string, unknown> = {};
  for (const param of definition.params || []) {
    if (param.name === "scenario_id") {
      template[param.name] = activeScenarioId || "";
      continue;
    }
    if (param.name === "scenario_root_dir") {
      const scenarioRoot = normalizePath(context.scenarioRootDir);
      if (scenarioRoot) {
        template[param.name] = scenarioRoot;
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(param, "default")) {
        template[param.name] = param.default;
        continue;
      }
      template[param.name] = "";
      continue;
    }
    if (param.name === "dem_path") {
      const demPath = normalizePath(context.demPath) || joinPath(normalizePath(context.scenarioRootDir), "dem.tif");
      if (demPath) {
        template[param.name] = demPath;
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(param, "default")) {
        template[param.name] = param.default;
        continue;
      }
      template[param.name] = "";
      continue;
    }
    if (param.name === "horizons_dir") {
      const horizonsDir = joinPath(normalizePath(context.scenarioRootDir), "lighting", "horizons");
      if (horizonsDir) {
        template[param.name] = horizonsDir;
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(param, "default")) {
        template[param.name] = param.default;
        continue;
      }
      template[param.name] = "";
      continue;
    }
    if (param.name === "output_path") {
      const outputPath = defaultOutputPath(definition, context);
      if (outputPath) {
        template[param.name] = outputPath;
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(param, "default")) {
        template[param.name] = param.default;
        continue;
      }
      template[param.name] = "";
      continue;
    }
    if (Object.prototype.hasOwnProperty.call(param, "default")) {
      template[param.name] = param.default;
      continue;
    }
    template[param.name] = sampleValue(param.type, param.name, activeScenarioId);
  }
  return template;
}

export function buildLaunchPayload(
  definition: ToolDefinition,
  parsedTemplate: Record<string, unknown>,
): Record<string, unknown> {
  if (definition.job_type === "notebook") return parsedTemplate;
  if ((definition.params || []).length !== 1) return parsedTemplate;
  const key = definition.params?.[0]?.name;
  if (!key) return parsedTemplate;
  if (Object.prototype.hasOwnProperty.call(parsedTemplate, key)) {
    const value = parsedTemplate[key];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
    return { [key]: value };
  }
  return parsedTemplate;
}

export function normalizePercent(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  const clamped = Math.max(0, Math.min(100, numeric));
  return Math.round(clamped * 10) / 10;
}

export function normalizeRunStatus(value: unknown, fallback: JobRunStatus = "queued"): JobRunStatus {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "queued") return "queued";
  if (raw === "running") return "running";
  if (raw === "completed") return "completed";
  if (raw === "failed") return "failed";
  if (raw === "cancelled") return "cancelled";
  if (raw === "idle") return "idle";
  return fallback;
}

export function runStatusFromEventName(eventName: string, fallback: JobRunStatus = "running"): JobRunStatus {
  const event = String(eventName || "").trim().toLowerCase();
  if (event === "job_queued") return "queued";
  if (event === "job_started" || event === "job_progress") return "running";
  if (event === "job_completed") return "completed";
  if (event === "job_failed") return "failed";
  if (event === "job_cancelled") return "cancelled";
  return fallback;
}

export function isTerminalRunStatus(status: JobRunStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function statusLabel(status: JobRunStatus): string {
  if (status === "queued") return "Queued";
  if (status === "running") return "Running";
  if (status === "completed") return "Completed";
  if (status === "failed") return "Failed";
  if (status === "cancelled") return "Cancelled";
  return "Idle";
}

export function messageLevelFromEventName(eventName: string): JobMessageLevel {
  const event = String(eventName || "").trim().toLowerCase();
  if (event === "job_progress") return "progress";
  if (event === "job_completed" || event === "job_started" || event === "job_queued") return "info";
  if (event === "job_failed") return "error";
  if (event === "job_cancelled") return "warn";
  return "system";
}

function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, Math.max(0, maxLength - 3))}...`;
}

export function summarizeJobResult(result: unknown): string | null {
  if (result === null || result === undefined) return null;
  if (typeof result === "string") {
    const trimmed = result.trim();
    return trimmed ? truncateText(trimmed, 200) : null;
  }
  if (typeof result === "number" || typeof result === "boolean") {
    return String(result);
  }
  if (Array.isArray(result) || isPlainRecord(result)) {
    try {
      return truncateText(JSON.stringify(result), 240);
    } catch {
      return "Result available";
    }
  }
  return "Result available";
}

export function buildJobEventMessage(eventName: string, data: Record<string, unknown>): string {
  const event = String(eventName || "").trim().toLowerCase();
  if (event === "job_queued") return "Run queued.";
  if (event === "job_started") return "Run started.";
  if (event === "job_progress") {
    const message = String(data.message || "").trim();
    if (message) return message;
    const percent = normalizePercent(data.percent);
    if (percent !== null) return `Progress updated: ${percent}%`;
    return "Progress updated.";
  }
  if (event === "job_completed") return "Run completed.";
  if (event === "job_failed") {
    const error = String(data.error || "").trim();
    return error ? `Run failed: ${error}` : "Run failed.";
  }
  if (event === "job_cancelled") return "Run cancelled.";
  return event ? `${event} event` : "Job event received.";
}

function inferTypeHint(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (isPlainRecord(value)) return "object";
  return typeof value;
}

export function typeHintIsNullable(typeHint: string): boolean {
  const normalized = String(typeHint || "").toLowerCase();
  return normalized.includes("none") || normalized.includes("null");
}

export function typeHintIsNumeric(typeHint: string): boolean {
  const normalized = String(typeHint || "").toLowerCase();
  return normalized.includes("int") || normalized.includes("float") || normalized.includes("number");
}

export function typeHintIsBoolean(typeHint: string): boolean {
  return String(typeHint || "").toLowerCase().includes("bool");
}

export function typeHintIsObject(typeHint: string): boolean {
  const normalized = String(typeHint || "").toLowerCase();
  return normalized.includes("dict") || normalized.includes("object") || normalized.includes("json");
}

function maybeDefaultNote(defaultValue: unknown): string {
  if (defaultValue === undefined) return "";
  if (defaultValue === null) return "default: null";
  if (typeof defaultValue === "string") return `default: ${defaultValue}`;
  return `default: ${JSON.stringify(defaultValue)}`;
}

export function buildParameterRows(
  definition: ToolDefinition | null,
  params: Record<string, unknown>,
): ParameterRow[] {
  const rows: ParameterRow[] = [];
  const knownTopLevel = new Set<string>();

  if (!definition) {
    for (const key of Object.keys(params).sort((a, b) => a.localeCompare(b))) {
      rows.push({
        key,
        path: [key],
        name: key,
        value: params[key],
        typeHint: inferTypeHint(params[key]),
        required: false,
        nullable: true,
        readOnly: false,
        note: "Custom parameter",
      });
    }
    return rows;
  }

  if (definition.job_type === "notebook") {
    const scenarioId = params.scenario_id;
    rows.push({
      key: "scenario_id",
      path: ["scenario_id"],
      name: "scenario_id",
      value: scenarioId,
      typeHint: "string",
      required: true,
      nullable: false,
      readOnly: false,
      note: "Active scenario",
    });
    knownTopLevel.add("scenario_id");

    const notebookJobId = params.notebook_job_id;
    rows.push({
      key: "notebook_job_id",
      path: ["notebook_job_id"],
      name: "notebook_job_id",
      value: notebookJobId,
      typeHint: "string",
      required: true,
      nullable: false,
      readOnly: true,
      note: "Notebook definition id",
    });
    knownTopLevel.add("notebook_job_id");

    const nestedParams = isPlainRecord(params.params) ? params.params : {};
    knownTopLevel.add("params");
    const nestedKeys = Object.keys(nestedParams).sort((a, b) => a.localeCompare(b));
    if (nestedKeys.length === 0) {
      rows.push({
        key: "params",
        path: ["params"],
        name: "params",
        value: nestedParams,
        typeHint: "object",
        required: false,
        nullable: false,
        readOnly: false,
        note: "Notebook params (advanced)",
      });
    } else {
      for (const key of nestedKeys) {
        const value = nestedParams[key];
        rows.push({
          key: `params.${key}`,
          path: ["params", key],
          name: `params.${key}`,
          value,
          typeHint: inferTypeHint(value),
          required: false,
          nullable: true,
          readOnly: false,
          note: "Notebook parameter",
        });
      }
    }
  } else {
    for (const param of definition.params || []) {
      const typeHint = String(param.type || inferTypeHint(params[param.name]));
      const required = Boolean(param.required);
      const nullable = typeHintIsNullable(typeHint) || Object.prototype.hasOwnProperty.call(param, "default");
      rows.push({
        key: param.name,
        path: [param.name],
        name: param.name,
        value: params[param.name],
        typeHint,
        required,
        nullable,
        readOnly: false,
        note: maybeDefaultNote(param.default),
      });
      knownTopLevel.add(param.name);
    }
  }

  for (const key of Object.keys(params).sort((a, b) => a.localeCompare(b))) {
    if (knownTopLevel.has(key)) continue;
    rows.push({
      key,
      path: [key],
      name: key,
      value: params[key],
      typeHint: inferTypeHint(params[key]),
      required: false,
      nullable: true,
      readOnly: false,
      note: "Additional parameter",
    });
  }

  return rows;
}

export function getValueAtPath(params: Record<string, unknown>, path: string[]): unknown {
  let cursor: unknown = params;
  for (const segment of path) {
    if (!isPlainRecord(cursor)) return undefined;
    cursor = cursor[segment];
  }
  return cursor;
}

export function setValueAtPath(
  params: Record<string, unknown>,
  path: string[],
  value: unknown,
): Record<string, unknown> {
  if (path.length === 0) return cloneRecord(params);
  const next = cloneRecord(params);
  let cursor: Record<string, unknown> = next;
  for (let i = 0; i < path.length - 1; i += 1) {
    const segment = path[i];
    const child = cursor[segment];
    if (isPlainRecord(child)) {
      cursor[segment] = cloneRecord(child);
    } else {
      cursor[segment] = {};
    }
    cursor = cursor[segment] as Record<string, unknown>;
  }
  cursor[path[path.length - 1]] = value;
  return next;
}

function formatValueForDisplay(value: unknown): string {
  if (value === null) return "(null)";
  if (value === undefined) return "(unset)";
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function appendSnapshotRows(rows: SnapshotRow[], keyPath: string, value: unknown): void {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      rows.push({ key: keyPath, valueText: "[]", typeHint: "array" });
      return;
    }
    for (let index = 0; index < value.length; index += 1) {
      const childPath = `${keyPath}[${index}]`;
      appendSnapshotRows(rows, childPath, value[index]);
    }
    return;
  }
  if (isPlainRecord(value)) {
    const keys = Object.keys(value).sort((a, b) => a.localeCompare(b));
    if (keys.length === 0) {
      rows.push({ key: keyPath, valueText: "{}", typeHint: "object" });
      return;
    }
    for (const child of keys) {
      const childPath = keyPath ? `${keyPath}.${child}` : child;
      appendSnapshotRows(rows, childPath, value[child]);
    }
    return;
  }
  rows.push({
    key: keyPath || "(root)",
    valueText: truncateText(formatValueForDisplay(value), 220),
    typeHint: inferTypeHint(value),
  });
}

export function buildSnapshotRows(params: Record<string, unknown>): SnapshotRow[] {
  const rows: SnapshotRow[] = [];
  const keys = Object.keys(params).sort((a, b) => a.localeCompare(b));
  if (keys.length === 0) {
    rows.push({ key: "(none)", valueText: "(empty)", typeHint: "empty" });
    return rows;
  }
  for (const key of keys) {
    appendSnapshotRows(rows, key, params[key]);
  }
  return rows;
}

export function createRunRecord(args: {
  runId: string;
  scenarioId: string | null;
  definitionId: string | null;
  title: string;
  status: JobRunStatus;
  paramsSnapshot: Record<string, unknown>;
  nowMs: number;
  percent?: number | null;
  latestMessage?: string;
  resultSummary?: string | null;
}): JobRunRecord {
  return {
    runId: args.runId,
    scenarioId: args.scenarioId,
    definitionId: args.definitionId,
    title: args.title,
    status: args.status,
    percent: args.percent ?? null,
    latestMessage: args.latestMessage || "",
    requestedAtMs: args.nowMs,
    startedAtMs: args.status === "running" ? args.nowMs : null,
    finishedAtMs: isTerminalRunStatus(args.status) ? args.nowMs : null,
    updatedAtMs: args.nowMs,
    paramsSnapshot: cloneRecord(args.paramsSnapshot),
    messages: [],
    resultSummary: args.resultSummary ?? null,
  };
}

export function applyJobEventToRun(
  run: JobRunRecord,
  eventName: string,
  data: Record<string, unknown>,
  timestampMs: number,
): JobRunRecord {
  const nextStatus = runStatusFromEventName(eventName, run.status === "idle" ? "running" : run.status);
  const reportedPercent = normalizePercent(data.percent);
  let percent = run.percent;
  if (reportedPercent !== null) {
    percent = reportedPercent;
  } else if (nextStatus === "completed") {
    percent = 100;
  }

  let requestedAtMs = run.requestedAtMs;
  let startedAtMs = run.startedAtMs;
  let finishedAtMs = run.finishedAtMs;

  if (eventName === "job_queued" && requestedAtMs === null) {
    requestedAtMs = timestampMs;
  }
  if ((eventName === "job_started" || eventName === "job_progress") && startedAtMs === null) {
    startedAtMs = timestampMs;
  }
  if (isTerminalRunStatus(nextStatus)) {
    finishedAtMs = timestampMs;
    if (nextStatus === "completed") percent = 100;
  }

  const text = buildJobEventMessage(eventName, data);
  const message: JobRunMessage = {
    id: `${run.runId}-${timestampMs}-${eventName}-${run.messages.length}`,
    timestampMs,
    eventName,
    level: messageLevelFromEventName(eventName),
    text,
    raw: cloneRecord(data),
  };

  let resultSummary = run.resultSummary;
  if (eventName === "job_completed") {
    resultSummary = summarizeJobResult(data.result);
  } else if (eventName === "job_failed" && !resultSummary) {
    resultSummary = String(data.error || "").trim() || null;
  }

  return {
    ...run,
    status: nextStatus,
    percent,
    latestMessage: text,
    requestedAtMs,
    startedAtMs,
    finishedAtMs,
    updatedAtMs: timestampMs,
    messages: [message, ...run.messages].slice(0, 150),
    resultSummary,
  };
}

export function upsertRecentRuns(
  runs: JobRunRecord[],
  updatedRun: JobRunRecord,
  limit = 15,
): JobRunRecord[] {
  const filtered = runs.filter((item) => item.runId !== updatedRun.runId);
  const merged = [updatedRun, ...filtered];
  merged.sort((a, b) => b.updatedAtMs - a.updatedAtMs);
  return merged.slice(0, Math.max(1, limit));
}

export function deriveDurationMs(
  startedAtMs: number | null,
  finishedAtMs: number | null,
  nowMs: number,
): number | null {
  if (startedAtMs === null) return null;
  if (finishedAtMs !== null) return Math.max(0, finishedAtMs - startedAtMs);
  return Math.max(0, nowMs - startedAtMs);
}

export function formatDurationMs(durationMs: number | null): string {
  if (durationMs === null) return "--";
  const totalSeconds = Math.floor(durationMs / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return `${hours}h ${String(remMinutes).padStart(2, "0")}m`;
}

export function formatTimestamp(timestampMs: number | null): string {
  if (timestampMs === null) return "--";
  try {
    return new Date(timestampMs).toLocaleString();
  } catch {
    return "--";
  }
}

function isMissingValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string" && value.trim() === "") return true;
  return false;
}

export function validateDraftParams(
  definition: ToolDefinition | null,
  params: Record<string, unknown>,
): string[] {
  const rows = buildParameterRows(definition, params);
  const errors: string[] = [];
  for (const row of rows) {
    const value = getValueAtPath(params, row.path);
    if (row.required && isMissingValue(value)) {
      errors.push(`Parameter "${row.name}" is required.`);
      continue;
    }
    if (isMissingValue(value)) continue;
    if (typeHintIsBoolean(row.typeHint) && typeof value !== "boolean") {
      errors.push(`Parameter "${row.name}" must be true/false.`);
      continue;
    }
    if (typeHintIsNumeric(row.typeHint) && typeof value !== "number") {
      errors.push(`Parameter "${row.name}" must be numeric.`);
      continue;
    }
    if (typeHintIsObject(row.typeHint) && !isPlainRecord(value) && !Array.isArray(value)) {
      errors.push(`Parameter "${row.name}" must be an object.`);
      continue;
    }
  }
  return errors;
}
