import { apiJson } from "./apiClient";

export type NomenclatureLocation = {
  kind: "point" | "region";
  center: { x: number; y: number; crs: string } | null;
  region: { min_x: number; min_y: number; max_x: number; max_y: number; crs: string } | null;
};

export type NomenclatureFeature = {
  feature_id: number;
  name: string;
  feature_type: string | null;
  location: NomenclatureLocation;
  description: string | null;
  diameter_km: number | null;
  importance_score: number;
  match_score?: number;
  distance_m?: number | null;
};

export async function searchNomenclature(
  query: string,
  options?: { featureType?: string; limit?: number },
): Promise<NomenclatureFeature[]> {
  const params = new URLSearchParams();
  params.set("query", query);
  if (options?.featureType) params.set("type", options.featureType);
  if (options?.limit) params.set("limit", String(options.limit));
  const payload = await apiJson<{ items?: NomenclatureFeature[] }>(`/api/v1/nomenclature/search?${params.toString()}`);
  return Array.isArray(payload.items) ? payload.items : [];
}

export async function resolveNomenclature(name: string, featureType?: string): Promise<NomenclatureFeature> {
  const params = new URLSearchParams();
  params.set("name", name);
  if (featureType) params.set("type", featureType);
  return apiJson<NomenclatureFeature>(`/api/v1/nomenclature/resolve?${params.toString()}`);
}

export async function nearbyNomenclature(
  x: number,
  y: number,
  options?: { featureType?: string; limit?: number; radiusM?: number },
): Promise<NomenclatureFeature[]> {
  const params = new URLSearchParams();
  params.set("x", String(x));
  params.set("y", String(y));
  if (options?.featureType) params.set("type", options.featureType);
  if (options?.limit) params.set("limit", String(options.limit));
  if (options?.radiusM) params.set("radius_m", String(options.radiusM));
  const payload = await apiJson<{ items?: NomenclatureFeature[] }>(`/api/v1/nomenclature/nearby?${params.toString()}`);
  return Array.isArray(payload.items) ? payload.items : [];
}
