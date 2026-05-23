export type ProductFile = {
  file_id: string;
  relative_path: string;
  media_type?: string;
  created_at_utc?: string;
};

export type RenderMode = "raster" | "vector";

export type PickedRenderableFile = {
  file: ProductFile;
  renderMode: RenderMode;
};

export function renderModeForFile(file: ProductFile): RenderMode | null {
  const path = String(file.relative_path || "").toLowerCase();
  const media = String(file.media_type || "").toLowerCase();
  if (media.includes("tiff") || path.endsWith(".tif") || path.endsWith(".tiff")) return "raster";
  if (
    media.includes("geo+json") ||
    media.includes("application/json") ||
    path.endsWith(".geojson") ||
    path.endsWith(".json")
  ) {
    return "vector";
  }
  return null;
}

export function pickRenderableFile(files: ProductFile[]): PickedRenderableFile | null {
  const sorted = [...files].sort((a, b) => String(a.created_at_utc || "").localeCompare(String(b.created_at_utc || ""))).reverse();
  for (const file of sorted) {
    const renderMode = renderModeForFile(file);
    if (renderMode) return { file, renderMode };
  }
  return null;
}

export function pickRenderableFileById(files: ProductFile[], preferredFileId: string | null): PickedRenderableFile | null {
  if (!preferredFileId) return null;
  const found = files.find((file) => file.file_id === preferredFileId);
  if (!found) return null;
  const renderMode = renderModeForFile(found);
  return renderMode ? { file: found, renderMode } : null;
}

export function buildLayerDiagnostics(style: Record<string, unknown>): {
  range: string;
  nodata: string;
  normalization: string;
} {
  const min = Number(style.valueMin);
  const max = Number(style.valueMax);
  const nodata = Number(style.nodataCutoff);
  const hasRange = Number.isFinite(min) && Number.isFinite(max) && max > min;
  return {
    range: hasRange ? `${min.toFixed(4)} .. ${max.toFixed(4)}` : "auto",
    nodata: Number.isFinite(nodata) ? `${nodata}` : "none",
    normalization: hasRange ? "valueMin/valueMax" : "fallback-band-clamp",
  };
}
