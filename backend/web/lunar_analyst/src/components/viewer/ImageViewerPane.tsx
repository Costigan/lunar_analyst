import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Menu, MenuItem, Popover, Spinner } from "@blueprintjs/core";
import proj4 from "proj4";
import {
  getScenarioImageMetadata,
  getScenarioImagePreviewUrl,
  type ScenarioImageMetadata,
  type ScenarioImageReadout,
} from "../../services/scenarioService";

type Props = {
  scenarioId: string;
  relativePath: string;
};

export default function ImageViewerPane(props: Props): JSX.Element {
  const { scenarioId, relativePath } = props;
  const [displayMode, setDisplayMode] = useState<"fit" | "original" | "panzoom">("panzoom");
  const [metadata, setMetadata] = useState<ScenarioImageMetadata | null>(null);
  const [readout, setReadout] = useState<ScenarioImageReadout | null>(null);
  const [statusText, setStatusText] = useState("Loading image metadata...");
  const [errorText, setErrorText] = useState("");
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [panZoomScale, setPanZoomScale] = useState(1);
  const [panZoomOffset, setPanZoomOffset] = useState({ x: 0, y: 0 });
  const [dragState, setDragState] = useState<{ active: boolean; lastX: number; lastY: number }>({
    active: false,
    lastX: 0,
    lastY: 0,
  });
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);

  const imageUrl = useMemo(
    () => `${getScenarioImagePreviewUrl(scenarioId, relativePath)}&cache_bust=${Date.now()}`,
    [relativePath, scenarioId],
  );

  useEffect(() => {
    setMetadata(null);
    setReadout(null);
    setErrorText("");
    setStatusText("Loading image metadata...");
    let cancelled = false;
    void (async () => {
      try {
        const response = await getScenarioImageMetadata(scenarioId, relativePath);
        if (cancelled) return;
        setMetadata(response);
        setStatusText("");
      } catch (error) {
        if (cancelled) return;
        setErrorText(error instanceof Error ? error.message : String(error));
        setStatusText("");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [relativePath, scenarioId]);

  const resetPanZoom = useCallback((): void => {
    const image = imageRef.current;
    const container = scrollRef.current;
    if (!image || !container) return;
    const width = image.naturalWidth || naturalSize?.width || 0;
    const height = image.naturalHeight || naturalSize?.height || 0;
    if (width <= 0 || height <= 0) return;
    const fitScale = Math.min(container.clientWidth / width, container.clientHeight / height);
    const normalizedScale = Number.isFinite(fitScale) && fitScale > 0 ? fitScale : 1;
    const scaledWidth = width * normalizedScale;
    const scaledHeight = height * normalizedScale;
    setPanZoomScale(normalizedScale);
    setPanZoomOffset({
      x: Math.max(0, (container.clientWidth - scaledWidth) / 2),
      y: Math.max(0, (container.clientHeight - scaledHeight) / 2),
    });
  }, [naturalSize?.height, naturalSize?.width]);

  useEffect(() => {
    if (displayMode !== "panzoom") return;
    const handleResize = (): void => {
      resetPanZoom();
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, [displayMode, resetPanZoom]);

  const selectDisplayMode = useCallback((nextMode: "fit" | "original" | "panzoom"): void => {
    setDisplayMode(nextMode);
    if (nextMode === "panzoom") {
      window.requestAnimationFrame(() => {
        resetPanZoom();
      });
    }
  }, [resetPanZoom]);

  const formatReadout = (): string => {
    if (errorText) return errorText;
    if (!readout) return metadata ? "Move the mouse over the image." : statusText;
    const parts = [`x=${readout.pixel.x} y=${readout.pixel.y}`];
    if (readout.projected.available && readout.projected.crs_name) {
      const projectionLabel = `${readout.projected.crs_name} projection`;
      parts.push(
        `${projectionLabel}: E=${Number(readout.projected.easting || 0).toFixed(2)} N=${Number(readout.projected.northing || 0).toFixed(2)}`,
      );
    }
    if (readout.geographic.available) {
      parts.push(
        `Lon=${Number(readout.geographic.longitude || 0).toFixed(6)} Lat=${Number(readout.geographic.latitude || 0).toFixed(6)}`,
      );
    }
    return parts.join(" • ");
  };

  const handleMouseMove = (event: React.MouseEvent<HTMLDivElement | HTMLImageElement>): void => {
    const image = imageRef.current;
    const meta = metadata;
    if (!image || !meta) return;
    const rect = image.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    if (
      event.clientX < rect.left
      || event.clientX > rect.right
      || event.clientY < rect.top
      || event.clientY > rect.bottom
    ) {
      setReadout(null);
      return;
    }
    const x = Math.min(
      meta.pixel_size.width - 1,
      Math.max(0, Math.floor(((event.clientX - rect.left) / rect.width) * meta.pixel_size.width)),
    );
    const y = Math.min(
      meta.pixel_size.height - 1,
      Math.max(0, Math.floor(((event.clientY - rect.top) / rect.height) * meta.pixel_size.height)),
    );
    const georef = meta.georeferencing;
    const projectedAvailable = Boolean(
      georef.is_georeferenced && georef.transform && georef.projection?.name,
    );
    let easting: number | null = null;
    let northing: number | null = null;
    if (projectedAvailable && georef.transform) {
      const u = x + 0.5;
      const v = y + 0.5;
      easting = (georef.transform.a * u) + (georef.transform.b * v) + georef.transform.c;
      northing = (georef.transform.d * u) + (georef.transform.e * v) + georef.transform.f;
    }
    let lon: number | null = null;
    let lat: number | null = null;
    if (
      projectedAvailable
      && easting !== null
      && northing !== null
      && georef.projection?.proj4
      && georef.geographic_crs_proj4
    ) {
      try {
        const [xLon, yLat] = proj4(georef.projection.proj4, georef.geographic_crs_proj4, [easting, northing]) as [number, number];
        if (Number.isFinite(xLon) && Number.isFinite(yLat)) {
          lon = xLon;
          lat = yLat;
        }
      } catch {
        lon = null;
        lat = null;
      }
    }

    setReadout((current) => {
      if (
        current
        && current.pixel.x === x
        && current.pixel.y === y
        && current.projected.available === projectedAvailable
        && Number(current.projected.easting ?? NaN) === Number(easting ?? NaN)
        && Number(current.projected.northing ?? NaN) === Number(northing ?? NaN)
        && current.geographic.available === (lon !== null && lat !== null)
        && Number(current.geographic.longitude ?? NaN) === Number(lon ?? NaN)
        && Number(current.geographic.latitude ?? NaN) === Number(lat ?? NaN)
      ) {
        return current;
      }
      return {
        scenario_id: scenarioId,
        relative_path: relativePath,
        pixel: {
          x,
          y,
          in_bounds: true,
        },
        projected: {
          available: projectedAvailable,
          crs_name: georef.projection?.name ?? null,
          easting,
          northing,
        },
        geographic: {
          available: lon !== null && lat !== null,
          longitude: lon,
          latitude: lat,
        },
      };
    });

    if (displayMode === "panzoom" && dragState.active) {
      const deltaX = event.clientX - dragState.lastX;
      const deltaY = event.clientY - dragState.lastY;
      setPanZoomOffset((current) => ({ x: current.x + deltaX, y: current.y + deltaY }));
      setDragState({ active: true, lastX: event.clientX, lastY: event.clientY });
    }
  };

  const handleMouseDown = (event: React.MouseEvent<HTMLDivElement>): void => {
    if (displayMode !== "panzoom" || event.button !== 0) return;
    event.preventDefault();
    setDragState({ active: true, lastX: event.clientX, lastY: event.clientY });
  };

  const handleMouseUp = (): void => {
    if (!dragState.active) return;
    setDragState((current) => ({ ...current, active: false }));
  };

  const handleWheel = (event: React.WheelEvent<HTMLDivElement>): void => {
    if (displayMode !== "panzoom") return;
    event.preventDefault();
    const image = imageRef.current;
    const container = scrollRef.current;
    if (!image || !container) return;
    const rect = image.getBoundingClientRect();
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    const imageX = pointerX / panZoomScale;
    const imageY = pointerY / panZoomScale;
    const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1;
    const nextScale = Math.min(32, Math.max(0.05, panZoomScale * factor));
    setPanZoomOffset((current) => ({
      x: event.clientX - container.getBoundingClientRect().left - (imageX * nextScale),
      y: event.clientY - container.getBoundingClientRect().top - (imageY * nextScale),
    }));
    setPanZoomScale(nextScale);
  };

  const displayModeLabel =
    displayMode === "fit" ? "Fit to Pane" : displayMode === "original" ? "Original Size" : "Pan + Zoom";

  return (
    <div className="workspace-tab-panel image-viewer-pane">
      <div className="image-viewer-toolbar">
        <div className="python-editor-title-group">
          <div className="python-editor-title">{relativePath}</div>
        </div>
        <div className="image-viewer-mode">
          <span>Display</span>
          <Popover
            placement="bottom-end"
            content={(
              <Menu>
                <MenuItem text="Fit to Pane" onClick={() => selectDisplayMode("fit")} />
                <MenuItem text="Original Size" onClick={() => selectDisplayMode("original")} />
                <MenuItem text="Pan + Zoom" onClick={() => selectDisplayMode("panzoom")} />
              </Menu>
            )}
          >
            <Button small rightIcon="caret-down" text={displayModeLabel} />
          </Popover>
        </div>
      </div>
      <div
        ref={scrollRef}
        className={`image-viewer-scroll ${displayMode === "panzoom" ? "image-viewer-scroll-panzoom" : ""}`}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => {
          setReadout(null);
          handleMouseUp();
        }}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onWheel={handleWheel}
      >
        <img
          ref={imageRef}
          src={imageUrl}
          alt={relativePath}
          className={
            displayMode === "fit"
              ? "image-viewer-image image-viewer-image-fit"
              : displayMode === "panzoom"
                ? "image-viewer-image image-viewer-image-panzoom"
                : "image-viewer-image"
          }
          style={
            displayMode === "panzoom"
              ? {
                  transform: `translate(${panZoomOffset.x}px, ${panZoomOffset.y}px) scale(${panZoomScale})`,
                  transformOrigin: "top left",
                }
              : undefined
          }
          onLoad={() => {
            const image = imageRef.current;
            if (!image) return;
            setNaturalSize({ width: image.naturalWidth, height: image.naturalHeight });
            if (displayMode === "panzoom") {
              window.requestAnimationFrame(() => {
                resetPanZoom();
              });
            }
          }}
        />
      </div>
      <div className="image-viewer-status-line image-viewer-status-line-bottom">
        {statusText && !metadata && !errorText ? <Spinner size={16} /> : null}
        <span>{formatReadout()}</span>
      </div>
    </div>
  );
}
