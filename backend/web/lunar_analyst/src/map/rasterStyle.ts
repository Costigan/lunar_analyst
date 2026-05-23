export type ColormapStop = {
  value: number;
  color: [number, number, number, number];
};

export type ColormapDefinition = {
  id: string;
  name: string;
  mode?: "continuous" | "discrete" | "threshold" | "cyclic";
  parameters?: Array<Record<string, unknown>>;
  cyclic?: Record<string, unknown>;
  stops: ColormapStop[];
};

export type RasterStyleState = {
  style_mode?: "colormap" | "contour";
  brightness?: number;
  contrast?: number;
  valueMin?: number;
  valueMax?: number;
  nodataCutoff?: number;
  alphaBand?: number;
  colormap?: string;
  threshold?: number;
  colormap_params?: Record<string, unknown>;
  contour?: {
    interval?: number;
    offset?: number;
    line_color?: [number, number, number, number];
    line_width_value?: number;
  };
};

export type RasterLayerState = {
  style?: RasterStyleState;
  source_file_id: string;
};

export function buildRasterStyle(
  layerState: RasterLayerState,
  colormaps: Record<string, ColormapDefinition>,
  fallbackColormapId = "gray",
): { color: unknown[] } {
  const style = layerState.style || {};
  const rawMin = Number(style.valueMin);
  const rawMax = Number(style.valueMax);
  const hasRange = Number.isFinite(rawMin) && Number.isFinite(rawMax) && rawMax > rawMin;
  const nodataValue = Number(style.nodataCutoff);
  const alphaBand = Number(style.alphaBand);
  const selected = String(style.colormap || fallbackColormapId);
  const colormapId = colormaps[selected] ? selected : fallbackColormapId;
  const cmap = colormaps[colormapId];
  const styleMode = String(style.style_mode || "colormap").toLowerCase();

  const normalized: unknown[] = hasRange
    ? ["clamp", ["/", ["-", ["band", 1], rawMin], rawMax - rawMin], 0, 1]
    : ["band", 1];
  const baseScalar: unknown[] = normalized;
  const thresholdOverride = Number(
    (style.colormap_params as Record<string, unknown> | undefined)?.threshold
      ?? style.threshold,
  );
  const hasThresholdOverride = Number.isFinite(thresholdOverride);
  const mode = String(cmap.mode || "continuous").toLowerCase();
  const effectiveStops = hasThresholdOverride && mode === "threshold"
    ? [
      { value: 0, color: cmap.stops[0].color },
      { value: Math.max(0, Math.min(1, thresholdOverride)), color: cmap.stops[0].color },
      { value: Math.max(0, Math.min(1, thresholdOverride + 0.0001)), color: cmap.stops[cmap.stops.length - 1].color },
      { value: 1, color: cmap.stops[cmap.stops.length - 1].color },
    ]
    : cmap.stops;
  const scalarForStops: unknown[] = baseScalar;

  const colormapChannel = (i: number): unknown[] => [
    "interpolate",
    ["linear"],
    scalarForStops,
    ...effectiveStops.flatMap((stop) => [stop.value, stop.color[i]]),
  ];

  const toneBrightness = Number(style.brightness ?? 0);
  const toneContrast = Number(style.contrast ?? 1);
  const tonedRgb = (i: number): unknown[] => [
    "*",
    255,
    [
      "clamp",
      [
        "+",
        [
          "+",
          [
            "*",
            ["-", ["/", colormapChannel(i), 255], 0.5],
            toneContrast,
          ],
          0.5,
        ],
        toneBrightness,
      ],
      0,
      1,
    ],
  ];

  const masks: unknown[] = [];
  if (Number.isFinite(alphaBand) && alphaBand >= 2) {
    masks.push(["==", ["band", alphaBand], 0]);
  }
  if (Number.isFinite(nodataValue)) {
    // OpenLayers GeoTIFF adds an alpha band for nodata and writes nodata pixels with alpha=0.
    masks.push(["==", ["band", 2], 0]);
    masks.push(["==", ["band", 1], nodataValue]);
  }
  if (hasRange) {
    masks.push(["<", ["band", 1], rawMin]);
  }

  if (styleMode === "contour") {
    const contour = (style.contour || {}) as Record<string, unknown>;
    const interval = Math.max(1e-6, Number(contour.interval ?? 1));
    const offset = Number(contour.offset ?? 0);
    const lineColorRaw = Array.isArray(contour.line_color) ? contour.line_color : [255, 255, 255, 1];
    const lineColor: [number, number, number, number] = [
      Number(lineColorRaw[0] ?? 255),
      Number(lineColorRaw[1] ?? 255),
      Number(lineColorRaw[2] ?? 255),
      Number(lineColorRaw[3] ?? 1),
    ];
    const widthValue = Math.max(1e-6, Number(contour.line_width_value ?? interval * 0.02));
    const remainder: unknown[] = ["%", ["-", ["band", 1], offset], interval];
    const distance: unknown[] = ["min", remainder, ["-", interval, remainder]];
    const lineMask: unknown[] = ["<=", distance, widthValue * 0.5];
    const contourColor: unknown[] = [
      "case",
      lineMask,
      ["color", lineColor[0], lineColor[1], lineColor[2], lineColor[3]],
      ["color", 0, 0, 0, 0],
    ];
    if (masks.length === 0) {
      return { color: contourColor };
    }
    const maskExpr: unknown[] = masks.length === 1 ? (masks[0] as unknown[]) : ["any", ...masks];
    return {
      color: [
        "case",
        maskExpr,
        ["color", 0, 0, 0, 0],
        contourColor,
      ],
    };
  }

  if (masks.length === 0) {
    return { color: ["color", tonedRgb(0), tonedRgb(1), tonedRgb(2), colormapChannel(3)] };
  }

  const maskExpr: unknown[] = masks.length === 1 ? (masks[0] as unknown[]) : ["any", ...masks];
  return {
    color: [
      "case",
      maskExpr,
      ["color", 0, 0, 0, 0],
      ["color", tonedRgb(0), tonedRgb(1), tonedRgb(2), colormapChannel(3)],
    ],
  };
}

export function buildRasterSourceSpec(sourceFileId: string, style: RasterStyleState = {}): Record<string, number | string> {
  const source: Record<string, number | string> = {
    url: `/api/v1/lunar-analyst/files/${sourceFileId}/raster`,
  };
  const min = Number(style.valueMin);
  const max = Number(style.valueMax);
  const nodata = Number(style.nodataCutoff);
  if (Number.isFinite(min) && Number.isFinite(max) && max > min) {
    source.min = min;
    source.max = max;
  }
  if (Number.isFinite(nodata)) {
    source.nodata = nodata;
  }
  return source;
}

export function rasterSourceSpecKey(style: RasterStyleState = {}): string {
  const min = Number(style.valueMin);
  const max = Number(style.valueMax);
  const nodata = Number(style.nodataCutoff);
  const alphaBand = Number(style.alphaBand);
  return JSON.stringify({
    min: Number.isFinite(min) ? min : null,
    max: Number.isFinite(max) ? max : null,
    nodata: Number.isFinite(nodata) ? nodata : null,
    alphaBand: Number.isFinite(alphaBand) ? alphaBand : null,
  });
}
