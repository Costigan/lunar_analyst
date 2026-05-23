import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ExplorerTreeRow } from "../components/explorer/FilteredTreeTable";
import type { TrekOverlayPatch, TrekOverlayState } from "../components/trek/TrekLayerCatalogPane";
import type { MapController } from "../map/mapController";
import { registerMapProjection } from "../map/projection";
import {
  bootstrapLunarAnalyst,
  fetchColormaps,
  fetchLunarAnalystConfig,
  fetchRasterStats,
  type ColormapDefinition,
} from "../services/lunarAnalystService";
import { createLayer, listProductFiles, type CreateLayerRequest } from "../services/layerService";
import type { ScenarioLayerState } from "../services/scenarioService";
import type { TrekLayerMetadata } from "../services/trekService";
import { pickRenderableFile, pickRenderableFileById, type ProductFile } from "../utils/layerManager";
import { applyRasterStatsStyle } from "../utils/rasterStatsStyle";
import {
  getScenarioScopedList,
  updateScenarioScopedList,
  type ScenarioScopedLists,
} from "../utils/trekOverlayScopes";
import { persistActiveScenarioId, selectBootstrapScenarioId } from "../utils/scenarioSelection";
import { useLayerSync } from "./useLayerSync";

function canonicalTrekOverlayId(metadata: TrekLayerMetadata): string {
  const uuid = String(metadata.item_UUID || "").trim();
  if (uuid.length > 0) return uuid;
  return String(metadata.productLabel || "").trim();
}

function trekExtentFromMetadata(metadata: TrekLayerMetadata): [number, number, number, number] | null {
  const raw = String(metadata.trekBbox || metadata.bbox || "").trim();
  if (!raw) return null;
  const parts = raw.split(",").map((value) => Number(value.trim()));
  if (parts.length !== 4 || parts.some((value) => !Number.isFinite(value))) return null;
  return [parts[0], parts[1], parts[2], parts[3]];
}

export type ScenarioWorkspaceState = {
  statusText: string;
  errorText: string;
  projection: ReturnType<typeof registerMapProjection> | null;
  center: [number, number];
  zoom: number;
  activeScenarioId: string | null;
  setActiveScenarioId: Dispatch<SetStateAction<string | null>>;
  activeScenarioIdRef: MutableRefObject<string | null>;
  scenarioLayers: ScenarioLayerState[];
  setScenarioLayers: Dispatch<SetStateAction<ScenarioLayerState[]>>;
  refreshScenarioLayers: (scenarioIdArg?: string | null) => Promise<void>;
  baseLayerVisible: boolean;
  setBaseLayerVisible: Dispatch<SetStateAction<boolean>>;
  hillshadeUrl: string;
  hillshadeOpacity: number;
  moonTrekCapabilitiesUrl: string;
  moonTrekLayerId: string;
  moonTrekMatrixSet: string;
  moonTrekStyle: string;
  extraZoomLevels: number;
  colormaps: ColormapDefinition[];
  trekOverlays: TrekOverlayState[];
  handleActiveScenarioChange: (scenarioId: string) => void;
  handleAddLayerFromExplorer: (row: ExplorerTreeRow) => Promise<void>;
  handleBaseLayerVisibleChange: (visible: boolean) => void;
  handleAddTrekOverlay: (metadata: TrekLayerMetadata) => void;
  handleRemoveTrekOverlay: (layerId: string) => void;
  handleUpdateTrekOverlay: (layerId: string, patch: TrekOverlayPatch) => void;
};

export function useScenarioWorkspace(
  mapControllerRef: MutableRefObject<MapController | null>,
): ScenarioWorkspaceState {
  const [statusText, setStatusText] = useState("Loading map...");
  const [errorText, setErrorText] = useState<string>("");
  const [activeScenarioId, setActiveScenarioId] = useState<string | null>(null);

  const [projection, setProjection] = useState<ReturnType<typeof registerMapProjection> | null>(null);
  const [center, setCenter] = useState<[number, number]>([0, 0]);
  const [zoom, setZoom] = useState(2);
  const [baseLayerVisible, setBaseLayerVisible] = useState(true);
  const [hillshadeUrl, setHillshadeUrl] = useState<string>("");
  const [hillshadeOpacity, setHillshadeOpacity] = useState<number>(0.85);
  const [moonTrekCapabilitiesUrl, setMoonTrekCapabilitiesUrl] = useState<string>("");
  const [moonTrekLayerId, setMoonTrekLayerId] = useState<string>("LRO_WAC_Mosaic_SPole60_100mp");
  const [moonTrekMatrixSet, setMoonTrekMatrixSet] = useState<string>("default028mm");
  const [moonTrekStyle, setMoonTrekStyle] = useState<string>("default");
  const [extraZoomLevels, setExtraZoomLevels] = useState<number>(14);
  const [colormaps, setColormaps] = useState<ColormapDefinition[]>([]);

  const [trekOverlaysByScenarioId, setTrekOverlaysByScenarioId] = useState<ScenarioScopedLists<TrekOverlayState>>({});
  const {
    scenarioLayers,
    setScenarioLayers,
    refreshScenarioLayers,
    activeScenarioIdRef,
  } = useLayerSync(activeScenarioId);

  const trekOverlays = useMemo(
    () => getScenarioScopedList(trekOverlaysByScenarioId, activeScenarioId),
    [trekOverlaysByScenarioId, activeScenarioId],
  );

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const requestedScenarioId = selectBootstrapScenarioId({
          locationSearch: window.location.search,
          storage: window.localStorage,
        });
        const bootPromise = (async (): Promise<Record<string, string>> => {
          try {
            return await bootstrapLunarAnalyst(requestedScenarioId);
          } catch (error) {
            if (!requestedScenarioId) throw error;
            console.warn(
              `[lunar-analyst][bootstrap] requested scenario_id not available (${requestedScenarioId}); falling back to configured default.`,
              error,
            );
            return bootstrapLunarAnalyst();
          }
        })();
        const [boot, config] = await Promise.all([
          bootPromise,
          fetchLunarAnalystConfig(),
        ]);
        if (!active) return;

        if (typeof boot.scenario_id === "string" && boot.scenario_id.length > 0) {
          setActiveScenarioId(boot.scenario_id);
          persistActiveScenarioId(boot.scenario_id, window.localStorage);
        }
        const nextColormaps = await (async (): Promise<ColormapDefinition[]> => {
          try {
            const registry = await fetchColormaps(boot.scenario_id);
            return Array.isArray(registry.colormaps) ? registry.colormaps : [];
          } catch (error) {
            console.warn("[lunar-analyst][colormaps] fetch failed; using fallback set", error);
            return [];
          }
        })();
        if (!active) return;
        setColormaps(nextColormaps);
        const mapProjection = registerMapProjection({
          code: config.projection.code,
          proj4: config.projection.proj4,
          extent: config.projection.extent as [number, number, number, number],
        });
        setProjection(mapProjection);
        setCenter((config.view.center || [0, 0]) as [number, number]);
        setZoom(Number(config.view.zoom || 2));
        setHillshadeUrl(String(config.hillshade?.url || ""));
        setHillshadeOpacity(Number(config.hillshade?.opacity ?? 0.85));
        setMoonTrekCapabilitiesUrl(String(config.moon_trek?.capabilities_url || ""));
        setMoonTrekLayerId(String(config.moon_trek?.layer || "LRO_WAC_Mosaic_SPole60_100mp"));
        setMoonTrekMatrixSet(String(config.moon_trek?.tile_matrix_set || "default028mm"));
        setMoonTrekStyle(String(config.moon_trek?.style || "default"));
        setExtraZoomLevels(Number(config.view?.extra_zoom_levels ?? 14));
        setStatusText("Map ready");
      } catch (error) {
        if (!active) return;
        const message = error instanceof Error ? error.message : String(error);
        setStatusText("Map failed");
        setErrorText(message);
      }
    })();

    return () => {
      active = false;
    };
  }, []);

  const handleActiveScenarioChange = useCallback((scenarioId: string) => {
    setActiveScenarioId(scenarioId);
    persistActiveScenarioId(scenarioId, window.localStorage);
  }, []);

  useEffect(() => {
    let active = true;
    if (!activeScenarioId) return () => { active = false; };
    void (async () => {
      try {
        const registry = await fetchColormaps(activeScenarioId);
        if (!active) return;
        setColormaps(Array.isArray(registry.colormaps) ? registry.colormaps : []);
      } catch (error) {
        if (!active) return;
        console.warn("[lunar-analyst][colormaps] refresh failed", error);
      }
    })();
    return () => {
      active = false;
    };
  }, [activeScenarioId]);

  const handleAddLayerFromExplorer = useCallback(
    async (row: ExplorerTreeRow): Promise<void> => {
      if (!row.node?.product_id || !row.node?.file_id) return;
      const targetScenarioId = row.scenarioId;
      if (targetScenarioId !== activeScenarioIdRef.current) {
        handleActiveScenarioChange(targetScenarioId);
      }

      try {
        const files = (await listProductFiles(row.node.product_id)) as ProductFile[];
        const picked = pickRenderableFileById(files, row.node.file_id) || pickRenderableFile(files);
        if (!picked) return;

        const style: Record<string, unknown> =
          picked.renderMode === "raster" ? { brightness: 0, contrast: 1 } : {};

        if (picked.renderMode === "raster") {
          try {
            const stats = await fetchRasterStats(picked.file.file_id);
            if (stats && Number.isFinite(Number(stats.min)) && Number.isFinite(Number(stats.max))) {
              style.valueMin = Number(stats.min);
              style.valueMax = Number(stats.max);
            }
            Object.assign(style, applyRasterStatsStyle(style, stats));
          } catch (error) {
            console.warn("Failed to fetch raster stats", error);
          }
        }

        const topZ = scenarioLayers.length
          ? Math.max(...scenarioLayers.map((layer) => Number(layer.z_index))) + 10
          : 10;

        const request: CreateLayerRequest = {
          scenario_id: targetScenarioId,
          product_id: row.node.product_id,
          title: row.name,
          visible: true,
          opacity: 1,
          z_index: topZ,
          render_mode: picked.renderMode,
          source_file_id: picked.file.file_id,
          style,
        };

        await createLayer(request);
        await refreshScenarioLayers(targetScenarioId);
      } catch (error) {
        console.error("Failed to add layer from explorer", error);
      }
    },
    [activeScenarioIdRef, handleActiveScenarioChange, refreshScenarioLayers, scenarioLayers],
  );

  const handleBaseLayerVisibleChange = useCallback((visible: boolean) => {
    setBaseLayerVisible(visible);
  }, []);

  const handleAddTrekOverlay = useCallback(
    (metadata: TrekLayerMetadata) => {
      const canonical = canonicalTrekOverlayId(metadata);
      const extent = trekExtentFromMetadata(metadata);
      setTrekOverlaysByScenarioId((prevByScenario) =>
        updateScenarioScopedList(prevByScenario, activeScenarioIdRef.current, (prev) => {
          const existing = prev.find((overlay) => canonicalTrekOverlayId(overlay.metadata) === canonical);
          if (existing) {
            return prev.map((overlay) =>
              overlay.layer_id === existing.layer_id ? { ...overlay, visible: true } : overlay,
            );
          }
          const fallback = String(metadata.productLabel || "").trim() || `trek_${Date.now()}`;
          const layerId = `trek_${canonical || fallback}`.replace(/[^a-zA-Z0-9_.:-]/g, "_");
          const topScenarioZ = scenarioLayers.length > 0
            ? Math.max(...scenarioLayers.map((layer) => Number(layer.z_index)))
            : 0;
          const topOverlayZ = prev.length > 0
            ? Math.max(...prev.map((overlay) => Number(overlay.z_index)))
            : 0;
          return [
            ...prev,
            {
              layer_id: layerId,
              metadata,
              visible: true,
              opacity: 1,
              z_index: Math.max(topScenarioZ, topOverlayZ) + 10,
              style: {
                brightness: 0,
                contrast: 1,
              },
            },
          ];
        }),
      );
      if (extent) {
        mapControllerRef.current?.fitExtent(extent, { paddingPx: 28 });
      }
    },
    [activeScenarioIdRef, mapControllerRef, scenarioLayers],
  );

  const handleRemoveTrekOverlay = useCallback((layerId: string) => {
    setTrekOverlaysByScenarioId((prevByScenario) =>
      updateScenarioScopedList(
        prevByScenario,
        activeScenarioIdRef.current,
        (prev) => prev.filter((overlay) => overlay.layer_id !== layerId),
      ),
    );
  }, [activeScenarioIdRef]);

  const handleUpdateTrekOverlay = useCallback((layerId: string, patch: TrekOverlayPatch) => {
    setTrekOverlaysByScenarioId((prevByScenario) =>
      updateScenarioScopedList(prevByScenario, activeScenarioIdRef.current, (prev) =>
        prev.map((overlay) => {
          if (overlay.layer_id !== layerId) return overlay;
          const patchStyle = patch.style && typeof patch.style === "object"
            ? patch.style
            : undefined;
          return {
            ...overlay,
            visible: patch.visible ?? overlay.visible,
            opacity: Number.isFinite(patch.opacity) ? Number(patch.opacity) : overlay.opacity,
            z_index: Number.isFinite(patch.z_index) ? Number(patch.z_index) : overlay.z_index,
            style: patchStyle ? { ...(overlay.style || {}), ...patchStyle } : (overlay.style || {}),
          };
        }),
      ),
    );
  }, [activeScenarioIdRef]);

  return {
    statusText,
    errorText,
    projection,
    center,
    zoom,
    activeScenarioId,
    setActiveScenarioId,
    activeScenarioIdRef,
    scenarioLayers,
    setScenarioLayers,
    refreshScenarioLayers,
    baseLayerVisible,
    setBaseLayerVisible,
    hillshadeUrl,
    hillshadeOpacity,
    moonTrekCapabilitiesUrl,
    moonTrekLayerId,
    moonTrekMatrixSet,
    moonTrekStyle,
    extraZoomLevels,
    colormaps,
    trekOverlays,
    handleActiveScenarioChange,
    handleAddLayerFromExplorer,
    handleBaseLayerVisibleChange,
    handleAddTrekOverlay,
    handleRemoveTrekOverlay,
    handleUpdateTrekOverlay,
  };
}
