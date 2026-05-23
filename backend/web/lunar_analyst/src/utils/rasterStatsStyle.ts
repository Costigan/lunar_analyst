import type { RasterStats } from "../services/lunarAnalystService";

export function applyRasterStatsStyle(
  style: Record<string, unknown>,
  stats: Pick<RasterStats, "nodata" | "alpha_band" | "min" | "max">,
): Record<string, unknown> {
  const next = { ...style };
  const min = stats.min;
  const max = stats.max;
  const nodata = stats.nodata;
  const alphaBand = stats.alpha_band;

  if (
    typeof min === "number" &&
    Number.isFinite(min) &&
    typeof max === "number" &&
    Number.isFinite(max) &&
    max > min
  ) {
    next.valueMin = min;
    next.valueMax = max;
  } else {
    delete next.valueMin;
    delete next.valueMax;
  }

  if (typeof nodata === "number" && Number.isFinite(nodata)) {
    next.nodataCutoff = nodata;
  } else if ("nodataCutoff" in next) {
    delete next.nodataCutoff;
  }

  if (typeof alphaBand === "number" && Number.isFinite(alphaBand)) {
    next.alphaBand = alphaBand;
  } else if ("alphaBand" in next) {
    delete next.alphaBand;
  }

  return next;
}
