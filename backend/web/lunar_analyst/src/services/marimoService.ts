import { apiJson } from "./apiClient";

export type MarimoLaunchRequest = {
  attach_url?: string;
  command?: string[];
  cwd?: string;
  scenario_id?: string;
  restart_if_running?: boolean;
};

export type MarimoStatus = {
  status: "running" | "stopped" | "attached";
  mode: "launch" | "attach" | "none";
  pid?: number | null;
  base_url?: string | null;
  log_path?: string | null;
  command?: string[];
  cwd?: string | null;
  started_at_utc?: string | null;
};

export type MarimoOpenNotebookRequest = {
  scenario_id: string;
  relative_path?: string;
  create_new?: boolean;
  restart_if_running?: boolean;
};

export type MarimoOpenNotebookResponse = {
  status: "ready";
  scenario_id: string;
  relative_path: string;
  absolute_file_path: string;
  file_url: string;
  file_name: string;
  notebook_capability: "marimo_notebook";
  created_new: boolean;
  modified_at_utc?: string | null;
};

export type NotebookOpenCapabilityRecord = {
  scenarioId: string;
  relativePath: string;
  modifiedAtUtc?: string | null;
  status: "openable" | "not_openable";
  checkedAtUtc: string;
};

const NOTEBOOK_CAPABILITY_STORAGE_KEY = "lunar-analyst:notebook-capability-cache";

export async function launchMarimo(payload: MarimoLaunchRequest): Promise<MarimoStatus> {
  return apiJson<MarimoStatus>("/api/v1/marimo/launch", { method: "POST", body: payload });
}

export async function launchMarimoForScenario(scenarioId: string): Promise<MarimoStatus> {
  return launchMarimo({ scenario_id: scenarioId, restart_if_running: true });
}

export async function marimoStatus(): Promise<MarimoStatus> {
  return apiJson<MarimoStatus>("/api/v1/marimo/status");
}

export async function openMarimoNotebook(
  payload: MarimoOpenNotebookRequest,
): Promise<MarimoOpenNotebookResponse> {
  return apiJson<MarimoOpenNotebookResponse>("/api/v1/marimo/open-notebook", {
    method: "POST",
    body: payload,
  });
}

export async function createMarimoNotebookForScenario(
  scenarioId: string,
): Promise<MarimoOpenNotebookResponse> {
  return openMarimoNotebook({
    scenario_id: scenarioId,
    create_new: true,
    restart_if_running: true,
  });
}

export function buildMarimoFileUrl(baseUrl: string, filePath: string): string {
  const baseOrigin =
    typeof window !== "undefined" && window.location?.origin
      ? window.location.origin
      : "http://127.0.0.1";
  const resolved = new URL(baseUrl, baseOrigin);
  resolved.searchParams.set("file", filePath);
  return resolved.toString();
}

function readCapabilityCache(storage: Storage | null | undefined): Record<string, NotebookOpenCapabilityRecord> {
  if (!storage) return {};
  try {
    const raw = storage.getItem(NOTEBOOK_CAPABILITY_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as Record<string, NotebookOpenCapabilityRecord>) : {};
  } catch {
    return {};
  }
}

function writeCapabilityCache(storage: Storage | null | undefined, value: Record<string, NotebookOpenCapabilityRecord>): void {
  if (!storage) return;
  storage.setItem(NOTEBOOK_CAPABILITY_STORAGE_KEY, JSON.stringify(value));
}

function capabilityKey(scenarioId: string, relativePath: string): string {
  return `${scenarioId}::${relativePath}`;
}

export function rememberNotebookOpenCapability(record: NotebookOpenCapabilityRecord): void {
  const storage = typeof window !== "undefined" ? window.localStorage : null;
  const cache = readCapabilityCache(storage);
  cache[capabilityKey(record.scenarioId, record.relativePath)] = record;
  writeCapabilityCache(storage, cache);
}

export function getNotebookOpenCapability(
  scenarioId: string,
  relativePath: string,
  modifiedAtUtc?: string | null,
): NotebookOpenCapabilityRecord | null {
  const storage = typeof window !== "undefined" ? window.localStorage : null;
  const record = readCapabilityCache(storage)[capabilityKey(scenarioId, relativePath)];
  if (!record) return null;
  if ((record.modifiedAtUtc || null) !== (modifiedAtUtc || null)) return null;
  return record;
}
