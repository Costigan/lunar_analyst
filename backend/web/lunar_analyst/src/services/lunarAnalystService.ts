import { apiJson } from "./apiClient";

export type LunarAnalystConfig = {
  projection: { code: string; proj4: string; extent: number[] };
  moon_trek: {
    capabilities_url: string;
    layer: string;
    tile_matrix_set: string;
    style: string;
  };
  hillshade: {
    url: string;
    native_url: string;
    opacity: number;
    path: string;
  };
  view: { center: number[]; zoom: number; extra_zoom_levels: number };
};

export type ColormapDefinition = {
  id: string;
  name: string;
  mode?: "continuous" | "discrete" | "threshold" | "cyclic";
  parameters?: Array<Record<string, unknown>>;
  cyclic?: Record<string, unknown>;
  stops: Array<{ value: number; color: [number, number, number, number] }>;
};

export type ColormapRegistry = {
  default: string;
  colormaps: ColormapDefinition[];
  rules?: Array<{ pattern: string; colormap: string }>;
  sources: {
    scenario_local: string;
    scenario_root: string;
    app: string;
    builtin: string;
  };
  rule_sources?: {
    scenario_local: string;
    scenario_root: string;
    app: string;
    builtin: string;
  };
};

export type RasterStats = {
  file_id: string;
  path: string;
  dtype: string;
  nodata: number | null;
  min: number | null;
  max: number | null;
  crs: string | null;
  band_count: number;
  alpha_band: number | null;
};

export async function fetchLunarAnalystConfig(): Promise<LunarAnalystConfig> {
  return apiJson<LunarAnalystConfig>("/api/v1/lunar-analyst/config");
}

export async function bootstrapLunarAnalyst(scenarioId?: string): Promise<Record<string, string>> {
  const query = scenarioId && scenarioId.trim().length > 0
    ? `?scenario_id=${encodeURIComponent(scenarioId.trim())}`
    : "";
  return apiJson<Record<string, string>>(`/api/v1/lunar-analyst/bootstrap${query}`, { method: "POST" });
}

export async function fetchColormaps(scenarioId?: string | null): Promise<ColormapRegistry> {
  const query = scenarioId && scenarioId.trim().length > 0
    ? `?scenario_id=${encodeURIComponent(scenarioId.trim())}`
    : "";
  return apiJson<ColormapRegistry>(`/api/v1/lunar-analyst/colormaps${query}`);
}

export async function applyDefaultColormap(layerId: string): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(
    `/api/v1/lunar-analyst/layers/${encodeURIComponent(layerId)}/apply-default-colormap`,
    { method: "POST" },
  );
}

export async function exportLayerRgba(
  layerId: string,
  payload: { output_relative_path?: string; overwrite_mode?: "ask" | "never" | "always" } = {},
): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(
    `/api/v1/lunar-analyst/layers/${encodeURIComponent(layerId)}/export-rgba`,
    { method: "POST", body: payload },
  );
}

export async function fetchRasterStats(fileId: string): Promise<RasterStats> {
  return apiJson<RasterStats>(`/api/v1/lunar-analyst/files/${encodeURIComponent(fileId)}/raster-stats`);
}
