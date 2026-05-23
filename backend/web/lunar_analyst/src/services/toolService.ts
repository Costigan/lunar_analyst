import { apiJson } from "./apiClient";

export type ToolDefinition = {
  job_definition_id: string;
  title: string;
  route_path: string;
  job_type: string;
  implementation_name?: string;
  handler_name?: string;
  visibility?: string;
  tags?: string[];
  params?: Array<{ name: string; type: string; required?: boolean; default?: unknown }>;
};

export async function listTools(scenarioId?: string): Promise<ToolDefinition[]> {
  const path = scenarioId
    ? `/api/v1/job-definitions?scenario_id=${encodeURIComponent(scenarioId)}`
    : "/api/v1/job-definitions";
  const payload = await apiJson<{ definitions?: ToolDefinition[] }>(path);
  return Array.isArray(payload?.definitions) ? payload.definitions : [];
}

export async function runTool(routePath: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(routePath, { method: "POST", body: payload });
}

export async function cancelRun(runId: string): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(`/api/v1/jobs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
}

export async function cancelJob(jobId: string): Promise<Record<string, unknown>> {
  return cancelRun(jobId);
}

export async function getRunStatus(runId: string): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(`/api/v1/jobs/${encodeURIComponent(runId)}`);
}

export async function getRunLogs(
  runId: string,
  options?: { stream?: "stdout" | "stderr" | "combined"; headLines?: number; tailLines?: number },
): Promise<Record<string, unknown>> {
  const stream = options?.stream || "combined";
  const headLines = options?.headLines ?? 0;
  const tailLines = options?.tailLines ?? 120;
  const query = new URLSearchParams({
    stream,
    head_lines: String(headLines),
    tail_lines: String(tailLines),
  });
  return apiJson<Record<string, unknown>>(`/api/v1/jobs/${encodeURIComponent(runId)}/logs?${query.toString()}`);
}
