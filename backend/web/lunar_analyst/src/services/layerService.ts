import { apiJson } from "./apiClient";
import type { ScenarioLayerState } from "./scenarioService";

export type CreateLayerRequest = {
  scenario_id: string;
  product_id: string;
  title: string;
  visible: boolean;
  opacity: number;
  z_index: number;
  render_mode: "raster" | "vector";
  source_file_id: string;
  style: Record<string, unknown>;
};

export async function createLayer(payload: CreateLayerRequest): Promise<ScenarioLayerState> {
  return apiJson<ScenarioLayerState>("/api/v1/layers", { method: "POST", body: payload });
}

export async function patchLayer(layerId: string, payload: Record<string, unknown>): Promise<ScenarioLayerState> {
  return apiJson<ScenarioLayerState>(`/api/v1/layers/${encodeURIComponent(layerId)}`, { method: "PATCH", body: payload });
}

export async function deleteLayer(layerId: string): Promise<null> {
  return apiJson<null>(`/api/v1/layers/${encodeURIComponent(layerId)}`, { method: "DELETE" });
}

export async function listProductFiles(productId: string): Promise<Array<Record<string, unknown>>> {
  return apiJson<Array<Record<string, unknown>>>(`/api/v1/products/${encodeURIComponent(productId)}/files`);
}
