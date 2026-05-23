import OlMap from "ol/Map.js";
import View from "ol/View.js";
import ScaleLine from "ol/control/ScaleLine.js";
import TileLayer from "ol/layer/Tile.js";
import WebGLTileLayer from "ol/layer/WebGLTile.js";
import VectorLayer from "ol/layer/Vector.js";
import XYZ from "ol/source/XYZ.js";
import GeoTIFF from "ol/source/GeoTIFF.js";
import VectorSource from "ol/source/Vector.js";
import GeoJSON from "ol/format/GeoJSON.js";
import TileGrid from "ol/tilegrid/TileGrid.js";
import { unByKey } from "ol/Observable.js";
import type Projection from "ol/proj/Projection.js";
import type { Extent } from "ol/extent.js";
import type BaseLayer from "ol/layer/Base.js";
import type { EventsKey } from "ol/events";
import {
  buildRasterStyle,
  buildRasterSourceSpec,
  rasterSourceSpecKey,
  type ColormapDefinition,
} from "./rasterStyle";
import { installDeferredGdalNodataPatch } from "./geotiffNodataPatch";
import {
  canonicalTrekLayerId,
  createTrekLayer,
} from "./trekLayerFactory";
import { resolveMoonTrekTileBaseUrl } from "./moonTrekUrl";
import { resolveMoonTrekBaseProfile } from "./moonTrekBaseProfile";
import type { TrekLayerMetadata } from "../services/trekService";

export type MapControllerConfig = {
  projection: Projection;
  center: [number, number];
  zoom: number;
  hillshadeUrl?: string;
  hillshadeOpacity?: number;
  moonTrekCapabilitiesUrl?: string;
  moonTrekLayerId?: string;
  moonTrekMatrixSet?: string;
  moonTrekStyle?: string;
  extraZoomLevels?: number;
};

export type ScenarioLayer = {
  layer_id: string;
  source_file_id: string;
  render_mode: "raster" | "vector";
  visible: boolean;
  opacity: number;
  z_index: number;
  style: Record<string, unknown>;
};

export type TrekOverlayLayer = {
  layer_id: string;
  metadata: TrekLayerMetadata;
  visible: boolean;
  opacity: number;
  z_index: number;
  style: Record<string, unknown>;
};

const FALLBACK_COLORMAPS: Record<string, ColormapDefinition> = {
  gray: {
    id: "gray",
    name: "Grayscale",
    stops: [
      { value: 0, color: [0, 0, 0, 1] as [number, number, number, number] },
      { value: 1, color: [255, 255, 255, 1] as [number, number, number, number] },
    ],
  },
  viridis: {
    id: "viridis",
    name: "Viridis",
    stops: [
      { value: 0, color: [68, 1, 84, 1] as [number, number, number, number] },
      { value: 0.25, color: [59, 82, 139, 1] as [number, number, number, number] },
      { value: 0.5, color: [33, 145, 140, 1] as [number, number, number, number] },
      { value: 0.75, color: [94, 201, 97, 1] as [number, number, number, number] },
      { value: 1, color: [253, 231, 37, 1] as [number, number, number, number] },
    ],
  },
  magma: {
    id: "magma",
    name: "Magma",
    stops: [
      { value: 0, color: [0, 0, 4, 1] as [number, number, number, number] },
      { value: 0.25, color: [81, 18, 123, 1] as [number, number, number, number] },
      { value: 0.5, color: [182, 55, 121, 1] as [number, number, number, number] },
      { value: 0.75, color: [251, 140, 60, 1] as [number, number, number, number] },
      { value: 1, color: [252, 253, 191, 1] as [number, number, number, number] },
    ],
  },
  inferno: {
    id: "inferno",
    name: "Inferno",
    stops: [
      { value: 0, color: [0, 0, 4, 1] as [number, number, number, number] },
      { value: 0.25, color: [87, 15, 109, 1] as [number, number, number, number] },
      { value: 0.5, color: [187, 55, 84, 1] as [number, number, number, number] },
      { value: 0.75, color: [249, 142, 8, 1] as [number, number, number, number] },
      { value: 1, color: [252, 255, 164, 1] as [number, number, number, number] },
    ],
  },
  plasma: {
    id: "plasma",
    name: "Plasma",
    stops: [
      { value: 0, color: [13, 8, 135, 1] as [number, number, number, number] },
      { value: 0.25, color: [126, 3, 167, 1] as [number, number, number, number] },
      { value: 0.5, color: [203, 71, 119, 1] as [number, number, number, number] },
      { value: 0.75, color: [248, 149, 64, 1] as [number, number, number, number] },
      { value: 1, color: [240, 249, 33, 1] as [number, number, number, number] },
    ],
  },
};

function buildColormapRegistry(colormaps: ColormapDefinition[]): Record<string, ColormapDefinition> {
  const next: Record<string, ColormapDefinition> = { ...FALLBACK_COLORMAPS };
  for (const item of colormaps) {
    const id = String(item?.id || "").trim();
    if (!id || !Array.isArray(item?.stops) || item.stops.length < 2) {
      continue;
    }
    next[id] = item;
  }
  return next;
}

const DEFAULT_MOON_TREK_LAYER_ID = "LRO_WAC_Mosaic_SPole60_100mp";
const DEFAULT_MOON_TREK_MATRIX_SET = "default028mm";
const DEFAULT_MOON_TREK_STYLE = "default";

installDeferredGdalNodataPatch();

export class MapController {
  private readonly map: OlMap;
  private readonly scenarioLayerObjects = new Map<string, WebGLTileLayer | VectorLayer<VectorSource>>();
  private readonly trekOverlayObjects = new Map<string, BaseLayer>();
  private readonly trekOverlayToneHandlers = new Map<string, { pre: EventsKey; post: EventsKey }>();
  private sizeChangeListenerKey: EventsKey | null = null;
  private pendingFitExtent: Extent | null = null;
  private pendingFitOptions: { paddingPx?: number; maxZoom?: number } | null = null;
  private pendingFitFrameId: number | null = null;
  private readonly moonTrekLayer: TileLayer<XYZ>;
  private colormaps: Record<string, ColormapDefinition>;

  private constructor(map: OlMap, moonTrekLayer: TileLayer<XYZ>) {
    this.map = map;
    this.moonTrekLayer = moonTrekLayer;
    this.colormaps = { ...FALLBACK_COLORMAPS };
    this.sizeChangeListenerKey = this.map.on("change:size", () => {
      this.tryFlushPendingFit("change:size");
    });
  }

  public static async create(target: HTMLElement, config: MapControllerConfig): Promise<MapController> {
    const moonTrek = await MapController.createMoonTrekLayer(config);
    const levels = Number.isFinite(config.extraZoomLevels)
      ? Math.max(0, Math.floor(Number(config.extraZoomLevels)))
      : 20;
    const extRes = [...moonTrek.resolutions];
    let minRes = extRes[extRes.length - 1];
    for (let idx = 0; idx < levels; idx += 1) {
      minRes /= 2;
      extRes.push(minRes);
    }
    const map = new OlMap({
      target,
      layers: [moonTrek.layer],
      maxTilesLoading: 64,
      view: new View({
        projection: moonTrek.projection,
        resolutions: extRes,
        constrainResolution: false, // Allow smooth, precise zooming to feature extents
        center: config.center,
        zoom: config.zoom,
      }),
    });
    map.addControl(
      new ScaleLine({
        bar: true,
        steps: 4,
        text: false,
        minWidth: 110,
        units: "metric",
      }),
    );
    return new MapController(map, moonTrek.layer);
  }

  private static async createMoonTrekLayer(
    config: MapControllerConfig,
  ): Promise<{ layer: TileLayer<XYZ>; projection: Projection; resolutions: number[] }> {
    const layerId = String(config.moonTrekLayerId || DEFAULT_MOON_TREK_LAYER_ID).trim() || DEFAULT_MOON_TREK_LAYER_ID;
    const matrixSet = String(config.moonTrekMatrixSet || DEFAULT_MOON_TREK_MATRIX_SET).trim() || DEFAULT_MOON_TREK_MATRIX_SET;
    const style = String(config.moonTrekStyle || DEFAULT_MOON_TREK_STYLE).trim() || DEFAULT_MOON_TREK_STYLE;
    const tileBaseUrl = resolveMoonTrekTileBaseUrl(config.moonTrekCapabilitiesUrl);
    const encodedLayerId = encodeURIComponent(layerId);
    // Use the application-registered projection object as the single source of truth.
    // Looking up EPSG::0 by code can resolve to a non-lunar fallback projection in some runtimes.
    const wmtsProjection = config.projection;
    const tileGridSpec = resolveMoonTrekBaseProfile(layerId, matrixSet);
    const tileGrid = new TileGrid({
      origin: tileGridSpec.origin,
      resolutions: tileGridSpec.resolutions,
      extent: tileGridSpec.extent,
      tileSize: 256,
    });
    const source = new XYZ({
      projection: wmtsProjection,
      tileGrid,
      interpolate: false,
      wrapX: false,
      tileUrlFunction: (coord) => {
        if (!coord) return "";
        const z = Number(coord[0]);
        const matrixId = tileGridSpec.matrixIds[z];
        if (!matrixId) return "";
        return `${tileBaseUrl}/${encodedLayerId}/1.0.0/${style}/${matrixSet}/${matrixId}/${coord[2]}/${coord[1]}.png`;
      },
    });
    let moonTrekTileErrorCount = 0;
    source.on("tileloaderror", () => {
      moonTrekTileErrorCount += 1;
      if (moonTrekTileErrorCount <= 5) {
        console.warn("[lunar-analyst][map] moon trek tileloaderror");
      } else if (moonTrekTileErrorCount === 6) {
        console.warn("[lunar-analyst][map] moon trek tileloaderror (further logs suppressed)");
      }
    });
    return {
      layer: new TileLayer({
        source,
        opacity: 1,
        zIndex: 0,
        visible: true,
      }),
      projection: wmtsProjection,
      resolutions: tileGridSpec.resolutions,
    };
  }

  public getMap(): OlMap {
    return this.map;
  }

  public setTarget(target: HTMLElement | undefined): void {
    this.map.setTarget(target);
    this.map.updateSize();
    this.tryFlushPendingFit("setTarget");
  }

  public destroy(): void {
    for (const layerId of this.trekOverlayToneHandlers.keys()) {
      this.clearTrekToneHandlers(layerId);
    }
    if (this.pendingFitFrameId !== null) {
      window.cancelAnimationFrame(this.pendingFitFrameId);
      this.pendingFitFrameId = null;
    }
    if (this.sizeChangeListenerKey) {
      unByKey(this.sizeChangeListenerKey);
      this.sizeChangeListenerKey = null;
    }
    this.map.setTarget(undefined);
  }

  public setBaseLayerVisible(visible: boolean): void {
    this.moonTrekLayer.setVisible(Boolean(visible));
  }

  public setColormaps(colormaps: ColormapDefinition[]): void {
    this.colormaps = buildColormapRegistry(colormaps);
  }

  public fitExtent(
    extent: Extent,
    options: {
      paddingPx?: number;
      maxZoom?: number;
    } = {},
  ): void {
    const requestedExtent: Extent = [
      Number(extent[0]),
      Number(extent[1]),
      Number(extent[2]),
      Number(extent[3]),
    ];

    this.map.updateSize();
    if (!this.hasRenderableViewport()) {
      this.pendingFitExtent = requestedExtent;
      this.pendingFitOptions = {
        paddingPx: options.paddingPx,
        maxZoom: options.maxZoom,
      };
      this.schedulePendingFitFlush("fitExtent_not_renderable");
      // eslint-disable-next-line no-console
      console.info("[lunar-analyst][map] fitExtent deferred: viewport not renderable", {
        extent: requestedExtent,
        options: this.pendingFitOptions,
      });
      return;
    }

    this.applyFitExtentNow(requestedExtent, options);
  }

  private applyFitExtentNow(
    extent: Extent,
    options: {
      paddingPx?: number;
      maxZoom?: number;
    },
    behavior: {
      source?: string;
      durationMs?: number;
    } = {},
  ): void {
    const paddingPx = Number.isFinite(options.paddingPx) ? Math.max(0, Number(options.paddingPx)) : 32;
    const durationMs = Number.isFinite(behavior.durationMs) ? Math.max(0, Number(behavior.durationMs)) : 250;

    let normalizedExtent: Extent = [
      Number(extent[0]),
      Number(extent[1]),
      Number(extent[2]),
      Number(extent[3]),
    ];
    let dx = normalizedExtent[2] - normalizedExtent[0];
    let dy = normalizedExtent[3] - normalizedExtent[1];

    // Diagnostic logging to help debug surprising fit results in the field.
    try {
      const mapSize = this.map.getSize();
      // eslint-disable-next-line no-console
      console.info("[lunar-analyst][map] fitExtent invoked", {
        extent: normalizedExtent,
        dx,
        dy,
        mapSize,
        options: {
          paddingPx,
          maxZoom: Number.isFinite(options.maxZoom) ? Number(options.maxZoom) : undefined,
          durationMs,
          source: behavior.source || "direct",
        },
      });

      if (dx <= 1e-6 || dy <= 1e-6) {
        const cx = (normalizedExtent[0] + normalizedExtent[2]) / 2;
        const cy = (normalizedExtent[1] + normalizedExtent[3]) / 2;
        const half = 1000; // 1km buffer fallback
        normalizedExtent = [cx - half, cy - half, cx + half, cy + half];
        dx = normalizedExtent[2] - normalizedExtent[0];
        dy = normalizedExtent[3] - normalizedExtent[1];
        // eslint-disable-next-line no-console
        console.warn("[lunar-analyst][map] fitExtent: expanded degenerate extent", { expanded: normalizedExtent });
      }
    } catch (err) {
      // ignore logging errors
    }

    const mapSize = this.map.getSize();
    if (!mapSize || mapSize.length < 2) return;
    const width = Math.max(2, Number(mapSize[0]) - (paddingPx * 2));
    const height = Math.max(2, Number(mapSize[1]) - (paddingPx * 2));
    const targetCenter: [number, number] = [
      (normalizedExtent[0] + normalizedExtent[2]) / 2,
      (normalizedExtent[1] + normalizedExtent[3]) / 2,
    ];

    const view = this.map.getView();
    const rawResolution = Math.max(dx / width, dy / height);
    let nextResolution = Number.isFinite(rawResolution) && rawResolution > 0
      ? rawResolution
      : Number(view.getResolution() || 1);
    if (Number.isFinite(options.maxZoom)) {
      const maxZoomResolution = view.getResolutionForZoom(Number(options.maxZoom));
      if (Number.isFinite(maxZoomResolution) && maxZoomResolution > 0) {
        // Larger resolution means less zoom-in; enforce this as an upper bound on zoom depth.
        nextResolution = Math.max(nextResolution, Number(maxZoomResolution));
      }
    }
    nextResolution = view.getConstrainedResolution(nextResolution);
    const constrainedCenter = view.getConstrainedCenter(targetCenter, nextResolution);

    if (durationMs > 0) {
      view.animate({
        center: constrainedCenter,
        resolution: nextResolution,
        duration: durationMs,
      });
    } else {
      view.setResolution(nextResolution);
      view.setCenter(constrainedCenter);
    }

    // eslint-disable-next-line no-console
    console.info("[lunar-analyst][map] fitExtent computed target", {
      extent: normalizedExtent,
      viewport: { width, height, paddingPx },
      rawResolution,
      constrainedResolution: nextResolution,
      targetCenter,
      constrainedCenter,
      durationMs,
    });

    try {
      const view = this.map.getView();
      // eslint-disable-next-line no-console
      console.info("[lunar-analyst][map] fitExtent applied", {
        center: view.getCenter(),
        zoom: view.getZoom(),
        resolution: view.getResolution(),
      });
    } catch (err) {
      // ignore logging errors
    }
  }

  private hasRenderableViewport(): boolean {
    const size = this.map.getSize();
    if (!size || size.length < 2) return false;
    if (Number(size[0]) < 2 || Number(size[1]) < 2) return false;
    const target = this.map.getTargetElement();
    if (!target || !target.isConnected) return false;
    const rect = target.getBoundingClientRect();
    if (Number(rect.width) < 2 || Number(rect.height) < 2) return false;
    const style = window.getComputedStyle(target);
    if (!style) return false;
    if (style.display === "none" || style.visibility === "hidden") return false;
    return true;
  }

  private schedulePendingFitFlush(reason: string): void {
    if (this.pendingFitFrameId !== null) return;
    this.pendingFitFrameId = window.requestAnimationFrame(() => {
      this.pendingFitFrameId = null;
      this.map.updateSize();
      this.tryFlushPendingFit(`raf:${reason}`);
    });
  }

  private tryFlushPendingFit(trigger: string): void {
    if (!this.pendingFitExtent || !this.pendingFitOptions) return;
    this.map.updateSize();
    if (!this.hasRenderableViewport()) {
      this.schedulePendingFitFlush(`retry:${trigger}`);
      return;
    }
    const extent = this.pendingFitExtent;
    const options = this.pendingFitOptions;
    this.pendingFitExtent = null;
    this.pendingFitOptions = null;
    // eslint-disable-next-line no-console
    console.info("[lunar-analyst][map] applying deferred fitExtent", { trigger, extent, options });
    this.applyFitExtentNow(extent, options, { source: "deferred", durationMs: 0 });
  }

  public syncScenarioLayers(layers: ScenarioLayer[]): void {
    const activeIds = new Set(layers.map((layer) => layer.layer_id));
    for (const [layerId, olLayer] of this.scenarioLayerObjects.entries()) {
      if (!activeIds.has(layerId)) {
        this.map.removeLayer(olLayer);
        this.scenarioLayerObjects.delete(layerId);
      }
    }

    const ordered = [...layers].sort((a, b) => Number(a.z_index) - Number(b.z_index));
    for (const layer of ordered) {
      let olLayer = this.scenarioLayerObjects.get(layer.layer_id);
      const sourceKey = layer.render_mode === "raster" ? rasterSourceSpecKey(layer.style || {}) : null;
      const needsReplace =
        !olLayer ||
        olLayer.get("source_file_id") !== layer.source_file_id ||
        olLayer.get("render_mode") !== layer.render_mode ||
        (layer.render_mode === "raster" && olLayer.get("source_spec_key") !== sourceKey);

      if (needsReplace) {
        if (olLayer) this.map.removeLayer(olLayer);
        if (layer.render_mode === "vector") {
          olLayer = new VectorLayer({
            source: new VectorSource({
              url: `/api/v1/lunar-analyst/files/${layer.source_file_id}/vector`,
              format: new GeoJSON(),
            }),
            visible: layer.visible,
            opacity: Number(layer.opacity),
            zIndex: Number(layer.z_index),
          });
        } else {
          const source = new GeoTIFF({
            sources: [buildRasterSourceSpec(layer.source_file_id, layer.style || {})],
            normalize: false,
            interpolate: false,
          });
          let loggedSourceError = false;
          source.on("change", () => {
            if (!loggedSourceError && source.getState() === "error") {
              loggedSourceError = true;
              console.warn(
                "[lunar-analyst][map] scenario raster source error",
                layer.source_file_id,
                source.getError(),
              );
            }
          });
          olLayer = new WebGLTileLayer({
            source,
            style: buildRasterStyle(
              { source_file_id: layer.source_file_id, style: layer.style || {} },
              this.colormaps,
            ),
            visible: layer.visible,
            opacity: Number(layer.opacity),
            zIndex: Number(layer.z_index),
            cacheSize: 2048,
          });
          olLayer.set("source_spec_key", sourceKey);
        }
        olLayer.set("source_file_id", layer.source_file_id);
        olLayer.set("render_mode", layer.render_mode);
        this.scenarioLayerObjects.set(layer.layer_id, olLayer);
        this.map.addLayer(olLayer);
      }

      olLayer.setVisible(layer.visible);
      olLayer.setOpacity(Number(layer.opacity));
      olLayer.setZIndex(Number(layer.z_index));
      if (layer.render_mode === "raster" && olLayer instanceof WebGLTileLayer) {
        olLayer.setStyle(
          buildRasterStyle(
            { source_file_id: layer.source_file_id, style: layer.style || {} },
            this.colormaps,
          ),
        );
      }
    }
  }

  public async syncTrekOverlays(overlays: TrekOverlayLayer[]): Promise<void> {
    const activeIds = new Set(overlays.map((layer) => layer.layer_id));
    for (const [layerId, olLayer] of this.trekOverlayObjects.entries()) {
      if (!activeIds.has(layerId)) {
        this.clearTrekToneHandlers(layerId);
        this.map.removeLayer(olLayer);
        this.trekOverlayObjects.delete(layerId);
      }
    }

    for (const overlay of overlays) {
      const canonical = canonicalTrekLayerId(overlay.metadata);
      let olLayer = this.trekOverlayObjects.get(overlay.layer_id);
      const needsReplace = !olLayer || String(olLayer.get("canonical_trek_id") || "") !== canonical;
      if (needsReplace) {
        if (olLayer) {
          this.clearTrekToneHandlers(overlay.layer_id);
          this.map.removeLayer(olLayer);
        }
        try {
          olLayer = await createTrekLayer(overlay.metadata, this.map.getView().getProjection());
        } catch (error) {
          console.warn("[lunar-analyst][map] failed to create trek overlay", overlay.layer_id, error);
          continue;
        }
        olLayer.set("layer_id", overlay.layer_id);
        olLayer.set("canonical_trek_id", canonical);
        this.trekOverlayObjects.set(overlay.layer_id, olLayer);
        this.map.addLayer(olLayer);
      }

      olLayer.setVisible(Boolean(overlay.visible));
      olLayer.setOpacity(Number(overlay.opacity));
      olLayer.setZIndex(Number(overlay.z_index));
      this.applyTrekToneStyle(overlay.layer_id, olLayer, overlay.style || {});
    }
  }

  private clearTrekToneHandlers(layerId: string): void {
    const handlers = this.trekOverlayToneHandlers.get(layerId);
    if (!handlers) return;
    unByKey(handlers.pre);
    unByKey(handlers.post);
    this.trekOverlayToneHandlers.delete(layerId);
  }

  private applyTrekToneStyle(layerId: string, layer: BaseLayer, style: Record<string, unknown>): void {
    this.clearTrekToneHandlers(layerId);
    const brightness = this.styleNumber(style, "brightness", 0);
    const contrast = this.styleNumber(style, "contrast", 1);
    const brightnessFactor = Math.max(0, 1 + brightness);
    const contrastFactor = Math.max(0, contrast);
    const isIdentity =
      Math.abs(brightnessFactor - 1) < 1e-6 &&
      Math.abs(contrastFactor - 1) < 1e-6;
    if (isIdentity) return;

    const filter = `brightness(${brightnessFactor}) contrast(${contrastFactor})`;
    const pre = layer.on("prerender", (event: unknown) => {
      const context = (event as { context?: CanvasRenderingContext2D }).context;
      if (!context) return;
      context.save();
      context.filter = filter;
    });
    const post = layer.on("postrender", (event: unknown) => {
      const context = (event as { context?: CanvasRenderingContext2D }).context;
      if (!context) return;
      context.restore();
    });
    this.trekOverlayToneHandlers.set(layerId, { pre, post });
  }

  private styleNumber(style: Record<string, unknown>, key: string, fallback: number): number {
    const value = Number(style[key]);
    return Number.isFinite(value) ? value : fallback;
  }
}
