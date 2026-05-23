import React, { useEffect, useRef } from "react";
import type Projection from "ol/proj/Projection.js";
import { MapController, type ScenarioLayer, type TrekOverlayLayer } from "./mapController";
import type { ColormapDefinition } from "./rasterStyle";

type MapViewportProps = {
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
  baseLayerVisible?: boolean;
  scenarioLayers?: ScenarioLayer[];
  trekOverlays?: TrekOverlayLayer[];
  colormaps?: ColormapDefinition[];
  onReady?: () => void;
  onControllerReady?: (controller: MapController | null) => void;
};

export default function MapViewport(props: MapViewportProps): JSX.Element {
  const {
    projection,
    center,
    zoom,
    hillshadeUrl,
    hillshadeOpacity,
    moonTrekCapabilitiesUrl,
    moonTrekLayerId,
    moonTrekMatrixSet,
    moonTrekStyle,
    extraZoomLevels,
    baseLayerVisible = true,
    scenarioLayers = [],
    trekOverlays = [],
    colormaps = [],
    onReady,
    onControllerReady,
  } = props;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const controllerRef = useRef<MapController | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);

  useEffect(() => {
    if (!containerRef.current || controllerRef.current) {
      return;
    }
    let cancelled = false;
    void (async () => {
      const controller = await MapController.create(containerRef.current as HTMLElement, {
        projection,
        center,
        zoom,
        hillshadeUrl,
        hillshadeOpacity,
        moonTrekCapabilitiesUrl,
        moonTrekLayerId,
        moonTrekMatrixSet,
        moonTrekStyle,
        extraZoomLevels,
      });
      if (cancelled) {
        controller.destroy();
        return;
      }
      controllerRef.current = controller;
      onControllerReady?.(controllerRef.current);
      controllerRef.current.setColormaps(colormaps);
      controllerRef.current.syncScenarioLayers(scenarioLayers);
      void controllerRef.current.syncTrekOverlays(trekOverlays);
      controllerRef.current.setBaseLayerVisible(baseLayerVisible);
      onReady?.();
    })();
    return () => {
      cancelled = true;
      resizeObserverRef.current?.disconnect();
      resizeObserverRef.current = null;
      onControllerReady?.(null);
      controllerRef.current?.destroy();
      controllerRef.current = null;
    };
  }, []);

  useEffect(() => {
    controllerRef.current?.syncScenarioLayers(scenarioLayers);
  }, [scenarioLayers]);

  useEffect(() => {
    if (!controllerRef.current) return;
    void controllerRef.current.syncTrekOverlays(trekOverlays);
  }, [trekOverlays]);

  useEffect(() => {
    if (!controllerRef.current) return;
    controllerRef.current.setColormaps(colormaps);
    controllerRef.current.syncScenarioLayers(scenarioLayers);
  }, [colormaps, scenarioLayers]);

  useEffect(() => {
    controllerRef.current?.setBaseLayerVisible(baseLayerVisible);
  }, [baseLayerVisible]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const observed = container.parentElement ?? container;
    const observer = new ResizeObserver(() => {
      window.requestAnimationFrame(() => {
        controllerRef.current?.getMap().updateSize();
      });
    });
    observer.observe(observed);
    resizeObserverRef.current = observer;
    return () => {
      observer.disconnect();
      if (resizeObserverRef.current === observer) {
        resizeObserverRef.current = null;
      }
    };
  }, []);

  return <div id="map" ref={containerRef} />;
}
