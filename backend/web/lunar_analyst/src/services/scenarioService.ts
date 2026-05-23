import { apiJson } from "./apiClient";

export type ScenarioSummary = {
  scenario_id: string;
  name: string;
  scenario_root: string;
  directory?: string;
  created_at_utc?: string;
  size_bytes?: number;
  primary_dem_path?: string;
  primary_dem_footprint?: unknown;
};

export type ExplorerNode = {
  node_type: string;
  name: string;
  relative_path: string;
  parent_relative_path?: string;
  product_id?: string;
  file_id?: string;
  is_renderable?: boolean;
  kind?: string;
  subkind?: string;
  created_at_utc?: string;
  modified_at_utc?: string;
  size_bytes?: number;
};

export type ScenarioLayerState = {
  layer_id: string;
  scenario_id: string;
  product_id: string;
  source_file_id: string;
  title: string;
  render_mode: "raster" | "vector";
  visible: boolean;
  opacity: number;
  z_index: number;
  style: Record<string, unknown>;
};

export type ScenarioPythonEntry = {
  scenario_id: string;
  relative_path: string;
  notebook_job_id: string;
  entry_kind: "marimo_notebook" | "script";
  title: string;
};

export type ScenarioTextFile = {
  scenario_id: string;
  relative_path: string;
  file_name: string;
  content: string;
  entry_kind: "marimo_notebook" | "script";
  modified_at_utc?: string | null;
};

export type ScenarioEditableFile = {
  scenario_id: string;
  relative_path: string;
  file_name: string;
  content: string;
  file_kind: "text" | "csv";
  modified_at_utc?: string | null;
};

export type ScenarioImageMetadata = {
  scenario_id: string;
  relative_path: string;
  file_name: string;
  media_type: string;
  pixel_size: {
    width: number;
    height: number;
  };
  georeferencing: {
    is_georeferenced: boolean;
    pixel_origin: "upper_left";
    transform?: {
      a: number;
      b: number;
      c: number;
      d: number;
      e: number;
      f: number;
    } | null;
    projection?: {
      crs_authority?: string | null;
      crs_code?: string | null;
      name: string;
      proj4?: string | null;
    } | null;
    bounds_projected?: {
      min_x: number;
      min_y: number;
      max_x: number;
      max_y: number;
    } | null;
    can_calculate_lonlat: boolean;
    geographic_crs_name?: string | null;
    geographic_crs_proj4?: string | null;
    lonlat_bounds?: {
      min_lon: number;
      min_lat: number;
      max_lon: number;
      max_lat: number;
    } | null;
  };
  modified_at_utc?: string | null;
};

export type ScenarioImageReadout = {
  scenario_id: string;
  relative_path: string;
  pixel: {
    x: number;
    y: number;
    in_bounds: boolean;
  };
  projected: {
    available: boolean;
    crs_name?: string | null;
    easting?: number | null;
    northing?: number | null;
  };
  geographic: {
    available: boolean;
    longitude?: number | null;
    latitude?: number | null;
  };
};

export type WorkspaceMessageEntry = {
  entry_id: string;
  scenario_id: string;
  created_at_utc: string;
  level: "info" | "success" | "warning" | "error";
  source: string;
  text: string;
};

export async function listScenarios(): Promise<ScenarioSummary[]> {
  return apiJson<ScenarioSummary[]>("/api/v1/scenarios");
}

export async function getScenario(scenarioId: string): Promise<ScenarioSummary> {
  return apiJson<ScenarioSummary>(`/api/v1/scenarios/${encodeURIComponent(scenarioId)}`);
}

export async function listScenarioProducts(scenarioId: string): Promise<Array<Record<string, unknown>>> {
  return apiJson<Array<Record<string, unknown>>>(`/api/v1/scenarios/${encodeURIComponent(scenarioId)}/products`);
}

export async function listExplorerNodes(scenarioId: string, includeHidden: boolean): Promise<ExplorerNode[]> {
  return apiJson<ExplorerNode[]>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/explorer-nodes?include_hidden=${includeHidden ? "true" : "false"}`,
  );
}

export async function listScenarioPythonEntries(scenarioId: string): Promise<ScenarioPythonEntry[]> {
  return apiJson<ScenarioPythonEntry[]>(`/api/v1/scenarios/${encodeURIComponent(scenarioId)}/python-entries`);
}

export async function createScenarioPythonFile(
  scenarioId: string,
  kind: "notebook" | "script",
): Promise<ScenarioTextFile> {
  return apiJson<ScenarioTextFile>(`/api/v1/scenarios/${encodeURIComponent(scenarioId)}/python-files`, {
    method: "POST",
    body: { kind },
  });
}

export async function readScenarioPythonFile(
  scenarioId: string,
  relativePath: string,
): Promise<ScenarioTextFile> {
  return apiJson<ScenarioTextFile>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/python-files?relative_path=${encodeURIComponent(relativePath)}`,
  );
}

export async function updateScenarioPythonFile(
  scenarioId: string,
  relativePath: string,
  content: string,
): Promise<ScenarioTextFile> {
  return apiJson<ScenarioTextFile>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/python-files?relative_path=${encodeURIComponent(relativePath)}`,
    {
      method: "PUT",
      body: { content },
    },
  );
}

export async function readScenarioEditableFile(
  scenarioId: string,
  relativePath: string,
): Promise<ScenarioEditableFile> {
  return apiJson<ScenarioEditableFile>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/editable-files?relative_path=${encodeURIComponent(relativePath)}`,
  );
}

export async function updateScenarioEditableFile(
  scenarioId: string,
  relativePath: string,
  content: string,
): Promise<ScenarioEditableFile> {
  return apiJson<ScenarioEditableFile>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/editable-files?relative_path=${encodeURIComponent(relativePath)}`,
    {
      method: "PUT",
      body: { content },
    },
  );
}

export function getScenarioRawFileUrl(scenarioId: string, relativePath: string): string {
  return `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/files:raw?relative_path=${encodeURIComponent(relativePath)}`;
}

export function getScenarioImagePreviewUrl(scenarioId: string, relativePath: string): string {
  return `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/image-preview?relative_path=${encodeURIComponent(relativePath)}`;
}

export async function getScenarioImageMetadata(
  scenarioId: string,
  relativePath: string,
): Promise<ScenarioImageMetadata> {
  return apiJson<ScenarioImageMetadata>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/image-metadata?relative_path=${encodeURIComponent(relativePath)}`,
  );
}

export async function getScenarioImageReadout(
  scenarioId: string,
  relativePath: string,
  pixelX: number,
  pixelY: number,
): Promise<ScenarioImageReadout> {
  return apiJson<ScenarioImageReadout>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/image-readout?relative_path=${encodeURIComponent(relativePath)}&pixel_x=${pixelX}&pixel_y=${pixelY}`,
  );
}

export async function lintScenarioPythonFile(
  scenarioId: string,
  relativePath: string,
): Promise<{ ok: boolean; stdout: string; stderr: string; returncode: number }> {
  return apiJson(`/api/v1/scenarios/${encodeURIComponent(scenarioId)}/python-files:lint`, {
    method: "POST",
    body: { relative_path: relativePath },
  });
}

export async function listWorkspaceMessages(scenarioId: string): Promise<WorkspaceMessageEntry[]> {
  const payload = await apiJson<{ entries?: WorkspaceMessageEntry[] }>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/messages`,
  );
  return Array.isArray(payload.entries) ? payload.entries : [];
}

export async function clearWorkspaceMessages(scenarioId: string): Promise<void> {
  await apiJson(`/api/v1/scenarios/${encodeURIComponent(scenarioId)}/messages`, { method: "DELETE" });
}

export async function listScenarioLayers(scenarioId: string): Promise<ScenarioLayerState[]> {
  return apiJson<ScenarioLayerState[]>(`/api/v1/scenarios/${encodeURIComponent(scenarioId)}/layers`);
}
