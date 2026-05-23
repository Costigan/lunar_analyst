import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchRasterStats } from "../services/lunarAnalystService";
import { listScenarioLayers, type ScenarioLayerState } from "../services/scenarioService";
import { applyRasterStatsStyle } from "../utils/rasterStatsStyle";

function layersDiffer(current: ScenarioLayerState[], next: ScenarioLayerState[]): boolean {
  if (current.length !== next.length) return true;
  for (let index = 0; index < next.length; index += 1) {
    if (
      current[index].layer_id !== next[index].layer_id ||
      current[index].z_index !== next[index].z_index ||
      current[index].visible !== next[index].visible ||
      current[index].opacity !== next[index].opacity ||
      JSON.stringify(current[index].style) !== JSON.stringify(next[index].style)
    ) {
      return true;
    }
  }
  return false;
}

export type LayerSyncState = {
  scenarioLayers: ScenarioLayerState[];
  setScenarioLayers: Dispatch<SetStateAction<ScenarioLayerState[]>>;
  refreshScenarioLayers: (scenarioIdArg?: string | null) => Promise<void>;
  activeScenarioIdRef: MutableRefObject<string | null>;
};

export function useLayerSync(activeScenarioId: string | null): LayerSyncState {
  const [scenarioLayers, setScenarioLayers] = useState<ScenarioLayerState[]>([]);
  const activeScenarioIdRef = useRef<string | null>(activeScenarioId);

  useEffect(() => {
    activeScenarioIdRef.current = activeScenarioId;
  }, [activeScenarioId]);

  const refreshScenarioLayers = useCallback(
    async (scenarioIdArg?: string | null): Promise<void> => {
      const scenarioId = scenarioIdArg ?? activeScenarioIdRef.current;
      if (!scenarioId) {
        setScenarioLayers((prev) => (prev.length === 0 ? prev : []));
        return;
      }
      try {
        const next = await listScenarioLayers(scenarioId);
        const hydrated = await Promise.all(
          next.map(async (layer) => {
            if (layer.render_mode !== "raster") {
              return layer;
            }
            try {
              const stats = await fetchRasterStats(layer.source_file_id);
              const style = applyRasterStatsStyle(
                { ...(layer.style || {}) } as Record<string, unknown>,
                stats,
              );
              return { ...layer, style };
            } catch (error) {
              console.warn("[lunar-analyst][layers] raster stats unavailable during hydration", error);
              return layer;
            }
          }),
        );
        setScenarioLayers((prev) => (layersDiffer(prev, hydrated) ? hydrated : prev));
      } catch (error) {
        console.error("[lunar-analyst][layers] refresh failed", error);
      }
    },
    [],
  );

  useEffect(() => {
    if (!activeScenarioId) {
      setScenarioLayers((prev) => (prev.length === 0 ? prev : []));
      return;
    }
    void refreshScenarioLayers(activeScenarioId);
  }, [activeScenarioId, refreshScenarioLayers]);

  return {
    scenarioLayers,
    setScenarioLayers,
    refreshScenarioLayers,
    activeScenarioIdRef,
  };
}
