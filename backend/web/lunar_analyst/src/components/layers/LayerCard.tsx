import React from "react";
import { Button, Slider, Switch, HTMLSelect, Icon, ContextMenu, Menu, MenuItem, Tooltip } from "@blueprintjs/core";
import type { ScenarioLayerState } from "../../services/scenarioService";
import type { ColormapDefinition } from "../../services/lunarAnalystService";
import { buildLayerDiagnostics } from "../../utils/layerManager";

type Props = {
  layer: ScenarioLayerState;
  expanded: boolean;
  onToggleExpanded: (layerId: string) => void;
  onSelect: (layerId: string) => void;
  onPatch: (
    layerId: string,
    payload: Record<string, unknown>,
    options?: { debounceMs?: number },
  ) => Promise<void>;
  onRemove: (layerId: string) => Promise<void>;
  onApplyDefaultColormap: (layerId: string) => Promise<void>;
  onExportRgba: (layerId: string) => Promise<void>;
  onDragStart: (layerId: string) => void;
  onDragEnd: () => void;
  colormaps: ColormapDefinition[];
  showToneControls?: boolean;
  showColormapControls?: boolean;
};

function styleNumber(style: Record<string, unknown>, key: string, fallback: number): number {
  const value = Number(style[key]);
  return Number.isFinite(value) ? value : fallback;
}

export default function LayerCard(props: Props): JSX.Element {
  const {
    layer,
    expanded,
    onToggleExpanded,
    onSelect,
    onPatch,
    onRemove,
    onApplyDefaultColormap,
    onExportRgba,
    onDragStart,
    onDragEnd,
    colormaps,
    showToneControls,
    showColormapControls,
  } = props;
  const style = layer.style || {};
  const diagnostics = buildLayerDiagnostics(style);
  const brightness = styleNumber(style, "brightness", 0);
  const contrast = styleNumber(style, "contrast", 1);
  const colormap = String(style.colormap || "gray");
  const enableToneControls = showToneControls ?? layer.render_mode === "raster";
  const enableColormapControls = showColormapControls ?? layer.render_mode === "raster";
  const fallbackColormapOptions = [
    { id: "gray", name: "Grayscale" },
    { id: "viridis", name: "Viridis" },
    { id: "magma", name: "Magma" },
    { id: "inferno", name: "Inferno" },
    { id: "plasma", name: "Plasma" },
  ];
  const colormapOptions =
    colormaps.length > 0
      ? colormaps
          .map((item) => ({ id: String(item.id || "").trim(), name: String(item.name || "").trim() }))
          .filter((item) => item.id.length > 0 && item.name.length > 0)
      : fallbackColormapOptions;
  const hasCurrentColormap = colormapOptions.some((item) => item.id === colormap);
  const selectOptions = hasCurrentColormap
    ? colormapOptions
    : [{ id: colormap, name: `${colormap} (current)` }, ...colormapOptions];
  const selectedColormap = colormaps.find((item) => String(item.id) === colormap);
  const selectedMode = String(selectedColormap?.mode || "").toLowerCase();
  const thresholdParamEntry = Array.isArray(selectedColormap?.parameters)
    ? (selectedColormap?.parameters.find(
      (entry) => String((entry as { id?: unknown }).id || "") === "threshold",
    ) as Record<string, unknown> | undefined)
    : undefined;
  const thresholdParamDefault = Number(thresholdParamEntry?.default ?? 0.5);
  const thresholdValue = Number(
    (style.colormap_params as Record<string, unknown> | undefined)?.threshold
      ?? style.threshold
      ?? thresholdParamDefault,
  );
  const showThresholdSlider = enableColormapControls && selectedMode === "threshold";

  return (
    <details
      className="layer-group reorderable bp6-layer-card"
      open={expanded}
      onToggle={(event) => {
        const isNowOpen = (event.target as HTMLDetailsElement).open;
        if (isNowOpen !== expanded) {
          onToggleExpanded(layer.layer_id);
        }
      }}
    >
      <summary
        className="layer-summary"
        onClick={() => onSelect(layer.layer_id)}
        onContextMenu={(event) => {
          event.preventDefault();
          ContextMenu.show(
            <Menu>
              <MenuItem
                icon="eye-open"
                text={layer.visible ? "Hide Layer" : "Show Layer"}
                onClick={() => void onPatch(layer.layer_id, { visible: !layer.visible })}
              />
              <MenuItem
                icon="trash"
                intent="danger"
                text="Remove Layer"
                onClick={() => void onRemove(layer.layer_id)}
              />
              {layer.render_mode === "raster" ? (
                <MenuItem
                  icon="tint"
                  text="Apply Default Colormap"
                  onClick={() => void onApplyDefaultColormap(layer.layer_id)}
                />
              ) : null}
              {layer.render_mode === "raster" ? (
                <MenuItem
                  icon="export"
                  text="Export as RGBA GeoTIFF"
                  onClick={() => void onExportRgba(layer.layer_id)}
                />
              ) : null}
              <Menu.Divider />
              <MenuItem icon="info-sign" text="Diagnostics" disabled />
            </Menu>,
            { left: event.clientX, top: event.clientY },
            () => {},
            true,
          );
        }}
      >
        <span
          className="layer-drag-handle"
          draggable
          onDragStart={(event) => {
            onDragStart(layer.layer_id);
            event.dataTransfer.setData("application/x-lunar-layer", JSON.stringify({ layer_id: layer.layer_id }));
            event.dataTransfer.setData("text/plain", layer.title);
          }}
          onDragEnd={() => onDragEnd()}
          title="Drag to reorder"
        >
          <Icon icon="drag-handle-vertical" size={14} style={{ opacity: 0.6 }} />
        </span>
        <Switch
          large={false}
          innerLabelChecked="on"
          innerLabel="off"
          checked={layer.visible}
          onChange={(event) => void onPatch(layer.layer_id, { visible: event.currentTarget.checked })}
          onClick={(event) => event.stopPropagation()}
          className="layer-visible-control"
          style={{ marginBottom: 0 }}
        />
        <span className="layer-title" style={{ flex: 1 }}>
          {layer.title}
        </span>
        {expanded ? (
          <Tooltip content="Remove Layer" intent="danger">
            <Button
              minimal
              small
              intent="danger"
              icon="trash"
              onClick={(event) => {
                event.stopPropagation();
                void onRemove(layer.layer_id);
              }}
            />
          </Tooltip>
        ) : null}
      </summary>
      <div className="layer-controls" style={{ padding: "8px 4px 4px 4px" }}>
        <label className="pattern-combobox-label">Opacity</label>
        <Slider
          min={0}
          max={1}
          stepSize={0.01}
          labelStepSize={0.5}
          value={layer.opacity}
          onChange={(value) => void onPatch(layer.layer_id, { opacity: value }, { debounceMs: 140 })}
        />

        {enableToneControls ? (
          <>
            <label className="pattern-combobox-label" style={{ marginTop: "8px" }}>
              Brightness
            </label>
            <Slider
              min={-1}
              max={1}
              stepSize={0.01}
              labelStepSize={1}
              value={brightness}
              onChange={(value) =>
                void onPatch(
                  layer.layer_id,
                  {
                    style: {
                      ...style,
                      brightness: value,
                    },
                  },
                  { debounceMs: 140 },
                )}
            />

            <label className="pattern-combobox-label" style={{ marginTop: "8px" }}>
              Contrast
            </label>
            <Slider
              min={0}
              max={4}
              stepSize={0.01}
              labelStepSize={1}
              value={contrast}
              onChange={(value) =>
                void onPatch(
                  layer.layer_id,
                  {
                    style: {
                      ...style,
                      contrast: value,
                    },
                  },
                  { debounceMs: 140 },
                )}
            />

            {enableColormapControls ? (
              <>
                <label className="pattern-combobox-label" style={{ marginTop: "8px", marginBottom: "4px" }}>
                  Colormap
                </label>
                <HTMLSelect
                  fill
                  value={colormap}
                  onChange={(event) =>
                    void onPatch(layer.layer_id, {
                      style: {
                        ...style,
                        colormap: event.currentTarget.value,
                      },
                    })}
                >
                  {selectOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </HTMLSelect>

                {showThresholdSlider ? (
                  <>
                    <label className="pattern-combobox-label" style={{ marginTop: "8px" }}>
                      Threshold
                    </label>
                    <Slider
                      min={0}
                      max={1}
                      stepSize={0.001}
                      labelStepSize={0.5}
                      value={Number.isFinite(thresholdValue) ? thresholdValue : thresholdParamDefault}
                      onChange={(value) =>
                        void onPatch(
                          layer.layer_id,
                          {
                            style: {
                              ...style,
                              colormap_params: {
                                ...((style.colormap_params as Record<string, unknown> | undefined) || {}),
                                threshold: value,
                              },
                              threshold: value,
                            },
                          },
                          { debounceMs: 80 },
                        )}
                    />
                  </>
                ) : null}

                <div className="layer-raster-diagnostics" style={{ marginTop: "8px" }}>
                  <div>range: {diagnostics.range}</div>
                  <div>nodata: {diagnostics.nodata}</div>
                  <div>normalization: {diagnostics.normalization}</div>
                </div>
              </>
            ) : null}
          </>
        ) : null}
      </div>
    </details>
  );
}
