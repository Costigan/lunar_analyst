import { apiJson } from "./apiClient";

export type TrekLayerMetadata = {
  item_UUID: string;
  productLabel: string;
  title?: string;
  description?: string;
  serviceTypes?: string[];
  productCat1?: string;
  [key: string]: unknown;
};

export type TrekLayersResponse = {
  layers: TrekLayerMetadata[];
  count: number;
  cached: boolean;
  fetched_at_utc: string;
  pattern?: string;
};

export type TrekLayerFeaturesResponse = {
  product_label: string;
  source_root_url: string;
  layer_ids: number[];
  feature_collection: {
    type: "FeatureCollection";
    features: Array<Record<string, unknown>>;
  };
  feature_count: number;
  cached: boolean;
  fetched_at_utc: string;
};

export async function listTrekLayers(forceRefresh = false): Promise<TrekLayersResponse> {
  const query = forceRefresh ? "?force_refresh=true" : "";
  return apiJson<TrekLayersResponse>(`/api/v1/trek/layers${query}`);
}

export async function searchTrekLayers(pattern: string, forceRefresh = false): Promise<TrekLayersResponse> {
  const params = new URLSearchParams();
  if (pattern.trim().length > 0) params.set("pattern", pattern.trim());
  if (forceRefresh) params.set("force_refresh", "true");
  const suffix = params.toString();
  return apiJson<TrekLayersResponse>(`/api/v1/trek/layers:search${suffix ? `?${suffix}` : ""}`);
}

export async function fetchTrekLayerFeatures(
  productLabel: string,
  layerId?: number,
  forceRefresh = false,
): Promise<TrekLayerFeaturesResponse> {
  const encoded = encodeURIComponent(productLabel.trim());
  const params = new URLSearchParams();
  if (Number.isInteger(layerId)) params.set("layer_id", String(layerId));
  if (forceRefresh) params.set("force_refresh", "true");
  const suffix = params.toString();
  return apiJson<TrekLayerFeaturesResponse>(
    `/api/v1/trek/layers/${encoded}/features${suffix ? `?${suffix}` : ""}`,
  );
}
