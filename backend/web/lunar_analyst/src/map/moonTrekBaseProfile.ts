export type MoonTrekBaseProfile = {
  origin: [number, number];
  extent: [number, number, number, number];
  resolutions: number[];
  matrixIds: string[];
};

const DEFAULT_LAYER_ID = "LRO_WAC_Mosaic_SPole60_100mp";
const DEFAULT_MATRIX_SET = "default028mm";

const DEFAULT_ORIGIN: [number, number] = [-1095930, 1095930];
const DEFAULT_EXTENT: [number, number, number, number] = [-931100, -931100, 931100, 931100];

function buildResolutions(baseResolution: number, levels: number): number[] {
  return Array.from({ length: Math.max(1, Math.floor(levels)) }, (_, idx) => baseResolution / (2 ** idx));
}

export function resolveMoonTrekBaseProfile(layerId: string, matrixSet: string): MoonTrekBaseProfile {
  const normalizedLayer = String(layerId || "").trim();
  const normalizedMatrixSet = String(matrixSet || "").trim();

  // Deterministic profile for the configured default Moon Trek south-pole base.
  // WMTS capabilities currently advertise matrices 0..4 for this product.
  if (normalizedLayer === DEFAULT_LAYER_ID && normalizedMatrixSet === DEFAULT_MATRIX_SET) {
    const resolutions = buildResolutions(8561.953125, 5);
    return {
      origin: DEFAULT_ORIGIN,
      extent: DEFAULT_EXTENT,
      resolutions,
      matrixIds: resolutions.map((_, idx) => String(idx)),
    };
  }

  // Generic fallback profile for non-default layers/matrix sets.
  const resolutions = buildResolutions(8561.95, 16);
  return {
    origin: DEFAULT_ORIGIN,
    extent: DEFAULT_EXTENT,
    resolutions,
    matrixIds: resolutions.map((_, idx) => String(idx)),
  };
}
