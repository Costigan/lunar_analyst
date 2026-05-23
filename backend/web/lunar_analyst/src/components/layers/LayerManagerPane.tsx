import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { InputGroup, Switch } from "@blueprintjs/core";
import type { TrekOverlayPatch, TrekOverlayState } from "../trek/TrekLayerCatalogPane";
import LayerCard from "./LayerCard";
import {
  createLayer,
  deleteLayer,
  listProductFiles,
  patchLayer,
  type CreateLayerRequest,
} from "../../services/layerService";
import {
  applyDefaultColormap,
  exportLayerRgba,
  fetchRasterStats,
  type ColormapDefinition,
} from "../../services/lunarAnalystService";
import type { ScenarioLayerState } from "../../services/scenarioService";
import {
  computeDropIndexForRow,
  planLayerReorderZPatches,
} from "../../utils/layerOrder";
import {
  pickRenderableFile,
  pickRenderableFileById,
  type ProductFile,
} from "../../utils/layerManager";
import { applyRasterStatsStyle } from "../../utils/rasterStatsStyle";
import { allTokensMatch, tokenizeFilter } from "../../utils/filterMatch";

type ProductDropPayload = {
  scenario_id: string;
  product_id: string;
  file_id?: string;
};

type LayerDropPayload = {
  layer_id: string;
};

type Props = {
  activeScenarioId: string | null;
  onActiveScenarioChange: (scenarioId: string) => void;
  baseLayerVisible: boolean;
  onBaseLayerVisibleChange: (visible: boolean) => void;
  layers: ScenarioLayerState[];
  colormaps: ColormapDefinition[];
  onLayersChange: React.Dispatch<React.SetStateAction<ScenarioLayerState[]>>;
  refreshScenarioLayers: (scenarioId?: string | null) => Promise<void>;
  trekOverlays: TrekOverlayState[];
  onUpdateTrekOverlay: (layerId: string, patch: TrekOverlayPatch) => void;
  onRemoveTrekOverlay: (layerId: string) => void;
};

type LayerPatchOptions = {
  debounceMs?: number;
};

function parseProductDropPayload(event: React.DragEvent): ProductDropPayload | null {
  const raw = event.dataTransfer.getData("application/x-lunar-product");
  if (!raw) return null;
  try {
    const payload = JSON.parse(raw) as ProductDropPayload;
    if (!payload?.scenario_id || !payload?.product_id) return null;
    if (payload.file_id !== undefined && typeof payload.file_id !== "string") return null;
    return payload;
  } catch {
    return null;
  }
}

function parseLayerDropPayload(event: React.DragEvent): LayerDropPayload | null {
  const raw = event.dataTransfer.getData("application/x-lunar-layer");
  if (!raw) return null;
  try {
    const payload = JSON.parse(raw) as LayerDropPayload;
    return payload?.layer_id ? payload : null;
  } catch {
    return null;
  }
}

function hasProductDragType(event: React.DragEvent): boolean {
  const types = Array.from(event.dataTransfer.types || []);
  return types.includes("application/x-lunar-product") || types.includes("text/plain");
}

function hasLayerDragType(event: React.DragEvent): boolean {
  const types = Array.from(event.dataTransfer.types || []);
  return types.includes("application/x-lunar-layer");
}

type DisplayLayerEntry = {
  kind: "scenario" | "trek";
  layer: ScenarioLayerState;
};

function topZIndex(layers: ScenarioLayerState[], trekOverlays: TrekOverlayState[]): number {
  const zValues = [
    ...layers.map((layer) => Number(layer.z_index)),
    ...trekOverlays.map((overlay) => Number(overlay.z_index)),
  ];
  const top = zValues.length ? Math.max(...zValues) : 0;
  return top + 10;
}

function isFeatureLikeMetadata(metadata: TrekOverlayState["metadata"]): boolean {
  const serviceTypes = Array.isArray(metadata.serviceTypes)
    ? metadata.serviceTypes.map((entry) => String(entry).toLowerCase())
    : [];
  const productCat = String(metadata.productCat1 || "").toLowerCase();
  return (
    serviceTypes.some((entry) => entry.includes("feature") || entry.includes("shape") || entry.includes("vector"))
    || productCat.includes("feature")
    || productCat.includes("shape")
    || productCat.includes("vector")
  );
}

function buildLayerCardStateFromTrekOverlay(overlay: TrekOverlayState): ScenarioLayerState {
  const productLabel = String(overlay.metadata.productLabel || overlay.layer_id);
  const title = String(overlay.metadata.title || productLabel || overlay.layer_id);
  return {
    layer_id: overlay.layer_id,
    scenario_id: "trek",
    product_id: `trek:${productLabel}`,
    source_file_id: productLabel,
    title,
    render_mode: isFeatureLikeMetadata(overlay.metadata) ? "vector" : "raster",
    visible: overlay.visible,
    opacity: overlay.opacity,
    z_index: Number(overlay.z_index),
    style: { ...(overlay.style || {}) },
  };
}

function mergePatchPayload(
  current: Record<string, unknown>,
  incoming: Record<string, unknown>,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...current, ...incoming };
  const currentStyle = current.style;
  const incomingStyle = incoming.style;
  if (
    currentStyle &&
    typeof currentStyle === "object" &&
    incomingStyle &&
    typeof incomingStyle === "object"
  ) {
    merged.style = {
      ...(currentStyle as Record<string, unknown>),
      ...(incomingStyle as Record<string, unknown>),
    };
  }
  return merged;
}

function applyLayerPatch(layer: ScenarioLayerState, payload: Record<string, unknown>): ScenarioLayerState {
  const next = { ...layer, ...payload } as ScenarioLayerState;
  const style = payload.style;
  if (style && typeof style === "object") {
    next.style = {
      ...(layer.style || {}),
      ...(style as Record<string, unknown>),
    };
  }
  return next;
}

function fileNameFromPath(path: string): string {
  const raw = String(path || "").trim();
  if (!raw) return "";
  const parts = raw.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

export default function LayerManagerPane(props: Props): JSX.Element {
  const {
    activeScenarioId,
    onActiveScenarioChange,
    baseLayerVisible,
    onBaseLayerVisibleChange,
    layers,
    colormaps,
    onLayersChange,
    refreshScenarioLayers,
    trekOverlays,
    onUpdateTrekOverlay,
    onRemoveTrekOverlay,
  } = props;
  const [expandedByLayerId, setExpandedByLayerId] = useState<Set<string>>(new Set());
  const [filterText, setFilterText] = useState("");
  const [draggingLayerId, setDraggingLayerId] = useState<string | null>(null);
  const [activeDropIndex, setActiveDropIndex] = useState<number | null>(null);
  const knownLayerIdsRef = useRef<Set<string>>(new Set());
  const patchTimerByLayerIdRef = useRef<Map<string, number>>(new Map());
  const pendingPatchByLayerIdRef = useRef<Map<string, Record<string, unknown>>>(new Map());

  const clearPendingPatchState = useCallback((): void => {
    for (const timerId of patchTimerByLayerIdRef.current.values()) {
      window.clearTimeout(timerId);
    }
    patchTimerByLayerIdRef.current.clear();
    pendingPatchByLayerIdRef.current.clear();
  }, []);

  useEffect(() => {
    knownLayerIdsRef.current = new Set();
    setExpandedByLayerId(new Set());
    clearPendingPatchState();
  }, [activeScenarioId, clearPendingPatchState]);

  useEffect(() => {
    setExpandedByLayerId((prev) => {
      const currentIds = new Set([
        ...layers.map((layer) => layer.layer_id),
        ...trekOverlays.map((overlay) => overlay.layer_id),
      ]);
      const next = new Set(prev);
      let changed = false;

      // Remove IDs no longer present
      for (const id of Array.from(next)) {
        if (!currentIds.has(id)) {
          next.delete(id);
          changed = true;
        }
      }

      // New layers start collapsed by default
      for (const layer of [...layers, ...trekOverlays.map((overlay) => buildLayerCardStateFromTrekOverlay(overlay))]) {
        if (!knownLayerIdsRef.current.has(layer.layer_id)) {
          // next.add(layer.layer_id); 
          changed = true;
        }
      }

      knownLayerIdsRef.current = currentIds;
      return changed ? next : prev;
    });
  }, [layers, trekOverlays]);

  useEffect(() => () => clearPendingPatchState(), [clearPendingPatchState]);

  const stackTopFirst = useMemo<DisplayLayerEntry[]>(
    () =>
      [
        ...layers.map((layer) => ({ kind: "scenario" as const, layer })),
        ...trekOverlays.map((overlay) => ({
          kind: "trek" as const,
          layer: buildLayerCardStateFromTrekOverlay(overlay),
        })),
      ].sort((a, b) => Number(b.layer.z_index) - Number(a.layer.z_index)),
    [layers, trekOverlays],
  );

  const stackPositionByLayerId = useMemo(() => {
    const map = new Map<string, number>();
    stackTopFirst.forEach((entry, index) => map.set(entry.layer.layer_id, index));
    return map;
  }, [stackTopFirst]);

  const filterTokens = useMemo(() => tokenizeFilter(filterText), [filterText]);

  const filteredStackTopFirst = useMemo(
    () =>
      stackTopFirst.filter((layer) => {
        if (!filterTokens.length) return true;
        const haystack = `${layer.layer.title} ${layer.layer.layer_id}`.toLowerCase();
        return allTokensMatch(filterTokens, haystack);
      }),
    [stackTopFirst, filterTokens],
  );

  const updateLayerLocally = useCallback(
    (layerId: string, payload: Record<string, unknown>): void => {
      onLayersChange((prev) =>
        prev.map((layer) => (layer.layer_id === layerId ? applyLayerPatch(layer, payload) : layer)),
      );
    },
    [onLayersChange],
  );

  const flushLayerPatch = useCallback(
    async (layerId: string): Promise<void> => {
      const payload = pendingPatchByLayerIdRef.current.get(layerId);
      if (!payload) return;
      pendingPatchByLayerIdRef.current.delete(layerId);
      try {
        const updated = await patchLayer(layerId, payload);
        onLayersChange((prev) =>
          prev.map((layer) =>
            layer.layer_id === layerId
              ? {
                  ...layer,
                  ...updated,
                  style: {
                    ...(layer.style || {}),
                    ...((updated.style || {}) as Record<string, unknown>),
                  },
                }
              : layer,
          ),
        );
      } catch (error) {
        console.error("[lunar-analyst][layers] patch failed", error);
        await refreshScenarioLayers();
      }
    },
    [onLayersChange, refreshScenarioLayers],
  );

  const patchLayerAndSync = useCallback(
    async (layerId: string, payload: Record<string, unknown>, options: LayerPatchOptions = {}): Promise<void> => {
      updateLayerLocally(layerId, payload);
      const existing = pendingPatchByLayerIdRef.current.get(layerId) || {};
      pendingPatchByLayerIdRef.current.set(layerId, mergePatchPayload(existing, payload));

      const existingTimer = patchTimerByLayerIdRef.current.get(layerId);
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
        patchTimerByLayerIdRef.current.delete(layerId);
      }

      const debounceMs = Math.max(0, Math.floor(Number(options.debounceMs || 0)));
      if (debounceMs > 0) {
        const timerId = window.setTimeout(() => {
          patchTimerByLayerIdRef.current.delete(layerId);
          void flushLayerPatch(layerId);
        }, debounceMs);
        patchTimerByLayerIdRef.current.set(layerId, timerId);
        return;
      }

      await flushLayerPatch(layerId);
    },
    [flushLayerPatch, updateLayerLocally],
  );

  const patchDisplayLayerAndSync = useCallback(
    async (layerId: string, payload: Record<string, unknown>, options: LayerPatchOptions = {}): Promise<void> => {
      const scenarioLayer = layers.find((layer) => layer.layer_id === layerId);
      if (scenarioLayer) {
        await patchLayerAndSync(layerId, payload, options);
        return;
      }
      const overlay = trekOverlays.find((entry) => entry.layer_id === layerId);
      if (!overlay) {
        return;
      }
      const stylePatch = payload.style;
      const opacityValue = Number(payload.opacity);
      const zIndexValue = Number(payload.z_index);
      onUpdateTrekOverlay(layerId, {
        visible: typeof payload.visible === "boolean" ? payload.visible : undefined,
        opacity: Number.isFinite(opacityValue) ? opacityValue : undefined,
        z_index: Number.isFinite(zIndexValue) ? zIndexValue : undefined,
        style:
          stylePatch && typeof stylePatch === "object"
            ? (stylePatch as Record<string, unknown>)
            : undefined,
      });
    },
    [layers, onUpdateTrekOverlay, patchLayerAndSync, trekOverlays],
  );

  async function removeLayerAndSync(layerId: string): Promise<void> {
    const overlay = trekOverlays.find((entry) => entry.layer_id === layerId);
    if (overlay) {
      onRemoveTrekOverlay(layerId);
      return;
    }
    const existingTimer = patchTimerByLayerIdRef.current.get(layerId);
    if (existingTimer !== undefined) {
      window.clearTimeout(existingTimer);
      patchTimerByLayerIdRef.current.delete(layerId);
    }
    pendingPatchByLayerIdRef.current.delete(layerId);
    onLayersChange((prev) => prev.filter((layer) => layer.layer_id !== layerId));
    try {
      await deleteLayer(layerId);
    } catch (error) {
      console.error("[lunar-analyst][layers] delete failed", error);
      await refreshScenarioLayers();
    }
  }

  async function applyLayerReorder(
    layerId: string,
    insertStackIndex: number,
    sourceLayers: ScenarioLayerState[] = layers,
    sourceTrekOverlays: TrekOverlayState[] = trekOverlays,
  ): Promise<void> {
    const patches = planLayerReorderZPatches(
      [
        ...sourceLayers.map((layer) => ({ layer_id: layer.layer_id, z_index: layer.z_index })),
        ...sourceTrekOverlays.map((overlay) => ({ layer_id: overlay.layer_id, z_index: overlay.z_index })),
      ],
      layerId,
      insertStackIndex,
      null,
    );
    if (!patches.length) return;
    const scenarioLayerIds = new Set(sourceLayers.map((layer) => layer.layer_id));
    const trekOverlayIds = new Set(sourceTrekOverlays.map((overlay) => overlay.layer_id));
    const zIndexByLayerId = new Map<string, number>(patches.map((patch) => [patch.layer_id, patch.z_index]));
    onLayersChange((prev) =>
      prev.map((layer) =>
        zIndexByLayerId.has(layer.layer_id) ? { ...layer, z_index: zIndexByLayerId.get(layer.layer_id) as number } : layer,
      ),
    );
    for (const patch of patches) {
      if (!trekOverlayIds.has(patch.layer_id)) continue;
      onUpdateTrekOverlay(patch.layer_id, { z_index: patch.z_index });
    }
    try {
      for (const patch of patches) {
        if (!scenarioLayerIds.has(patch.layer_id)) continue;
        await patchLayer(patch.layer_id, { z_index: patch.z_index });
      }
    } catch (error) {
      console.error("[lunar-analyst][layers] reorder failed", error);
      await refreshScenarioLayers();
    }
  }

  async function addProductLayerAtIndex(payload: ProductDropPayload, insertStackIndex: number): Promise<void> {
    if (!payload.scenario_id || !payload.product_id) return;
    const targetScenarioId = payload.scenario_id;
    const isActiveScenario = targetScenarioId === activeScenarioId;
    if (!isActiveScenario) {
      onActiveScenarioChange(targetScenarioId);
    }

    const files = (await listProductFiles(payload.product_id)) as ProductFile[];
    const picked = pickRenderableFileById(files, payload.file_id || null) || pickRenderableFile(files);
    if (!picked) return;

    const style: Record<string, unknown> =
      picked.renderMode === "raster" ? { brightness: 0, contrast: 1 } : {};
    if (picked.renderMode === "raster") {
      try {
        const stats = await fetchRasterStats(picked.file.file_id);
        const min = Number(stats.min);
        const max = Number(stats.max);
        if (Number.isFinite(min) && Number.isFinite(max) && max > min) {
          style.valueMin = min;
          style.valueMax = max;
        }
        Object.assign(style, applyRasterStatsStyle(style, stats));
      } catch (error) {
        console.warn("[lunar-analyst][layers] raster stats unavailable", error);
      }
    }

    const title = fileNameFromPath(picked.file.relative_path) || `Product ${payload.product_id.slice(0, 8)}`;
    const request: CreateLayerRequest = {
      scenario_id: targetScenarioId,
      product_id: payload.product_id,
      title,
      visible: true,
      opacity: 1,
      z_index: isActiveScenario ? topZIndex(layers, trekOverlays) : 10,
      render_mode: picked.renderMode,
      source_file_id: picked.file.file_id,
      style,
    };

    const created = await createLayer(request);
    if (!isActiveScenario) {
      await refreshScenarioLayers(targetScenarioId);
      return;
    }

    onLayersChange((prev) => [...prev, created]);
    await applyLayerReorder(created.layer_id, insertStackIndex, [...layers, created], trekOverlays);
  }

  const renderDropZone = (index: number, prominent = false) => (
    <div
      key={`drop-${index}`}
      className={`layer-drop-zone ${prominent ? "prominent" : ""} ${activeDropIndex === index ? "active-drop" : ""}`}
      aria-label={`Drop at position ${index}`}
      onDragOver={(event) => {
        const hasLayer = hasLayerDragType(event) || Boolean(draggingLayerId);
        const hasProduct = hasProductDragType(event);
        if (!hasLayer && !hasProduct) return;
        event.preventDefault();
        setActiveDropIndex(index);
      }}
      onDragLeave={() => {
        setActiveDropIndex((current) => (current === index ? null : current));
      }}
      onDrop={(event) => {
        event.preventDefault();
        event.stopPropagation();
        const layerPayload = parseLayerDropPayload(event);
        const productPayload = parseProductDropPayload(event);
        setActiveDropIndex(null);
        setDraggingLayerId(null);
        if (layerPayload?.layer_id) {
          void applyLayerReorder(layerPayload.layer_id, index);
          return;
        }
        if (productPayload) {
          void addProductLayerAtIndex(productPayload, index);
        }
      }}
    >
      {prominent ? <span className="layer-drop-zone-label">Drop product here to add a layer</span> : null}
    </div>
  );

  return (
    <div
      className="layer-panel-body"
      onDragOver={(event) => {
        if (hasProductDragType(event) || hasLayerDragType(event) || Boolean(draggingLayerId)) {
          event.preventDefault();
        }
      }}
      onDrop={(event) => {
        if (event.defaultPrevented || stackTopFirst.length) return;
        const productPayload = parseProductDropPayload(event);
        if (productPayload) {
          event.preventDefault();
          event.stopPropagation();
          void addProductLayerAtIndex(productPayload, 0);
        }
      }}
    >
      <label className="pattern-combobox-label" htmlFor="layer-filter-input">
        Layer Filter
      </label>
      <InputGroup
        placeholder="Type layer pattern"
        value={filterText}
        onChange={(event) => setFilterText(event.target.value)}
      />

      <div id="scenario-layer-list" className={draggingLayerId ? "drag-active" : ""}>
        {renderDropZone(0, stackTopFirst.length === 0)}
        {filteredStackTopFirst.map((entry) => {
          const layer = entry.layer;
          const entryPos = stackPositionByLayerId.get(layer.layer_id) ?? 0;
          const dropIdx = computeDropIndexForRow(entryPos, draggingLayerId, stackPositionByLayerId) ?? entryPos;
          return (
            <React.Fragment key={layer.layer_id}>
              <LayerCard
                layer={layer}
                expanded={expandedByLayerId.has(layer.layer_id)}
                onToggleExpanded={(layerId) => {
                  setExpandedByLayerId((prev) => {
                    const next = new Set(prev);
                    if (next.has(layerId)) next.delete(layerId);
                    else next.add(layerId);
                    return next;
                  });
                }}
                onSelect={() => undefined}
                onPatch={patchDisplayLayerAndSync}
                onRemove={removeLayerAndSync}
                onApplyDefaultColormap={async (layerId) => {
                  try {
                    await applyDefaultColormap(layerId);
                    await refreshScenarioLayers();
                  } catch (error) {
                    console.error("[lunar-analyst][layers] apply default colormap failed", error);
                  }
                }}
                onExportRgba={async (layerId) => {
                  try {
                    await exportLayerRgba(layerId, { overwrite_mode: "ask" });
                  } catch (error) {
                    console.error("[lunar-analyst][layers] export rgba launch failed", error);
                  }
                }}
                onDragStart={(layerId) => setDraggingLayerId(layerId)}
                onDragEnd={() => {
                  setDraggingLayerId(null);
                  setActiveDropIndex(null);
                }}
                colormaps={colormaps}
                showToneControls={entry.kind === "trek" ? true : undefined}
                showColormapControls={entry.kind === "trek" ? false : undefined}
              />
              {renderDropZone(dropIdx + 1, false)}
            </React.Fragment>
          );
        })}
        {!filteredStackTopFirst.length && stackTopFirst.length ? (
          <div className="filtered-list-empty">No matching layers.</div>
        ) : null}
      </div>

      <details className="layer-group layer-group-base" open>
        <summary className="layer-summary">
          <Switch
            checked={baseLayerVisible}
            onChange={(event) => onBaseLayerVisibleChange(event.currentTarget.checked)}
            onClick={(event) => event.stopPropagation()}
            className="layer-visible-control"
            style={{ marginBottom: 0 }}
          />
          <span className="layer-title">Moon Trek Base (drawn below scenario layers)</span>
        </summary>
        <div className="layer-controls">
          <div className="job-active">Base map only. Scenario layers render above this layer.</div>
        </div>
      </details>
    </div>
  );
}
