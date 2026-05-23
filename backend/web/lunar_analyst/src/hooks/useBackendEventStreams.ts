import type { MutableRefObject } from "react";
import { useEffect, useRef } from "react";
import type { MapController } from "../map/mapController";
import { connectAssistantEventsSocket, type AssistantWsEvent } from "../services/assistantWsClient";
import { connectEventsSocket, parseMapZoomRequested, type WsEventMessage } from "../services/wsClient";

function wsEndpoint(): string {
  return `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/api/v1/events`;
}

export type WorkspaceEventDecision =
  | { kind: "ignore" }
  | {
      kind: "zoom";
      extent: [number, number, number, number];
      paddingPx?: number;
      maxZoom?: number;
    }
  | {
      kind: "refresh";
      refreshLayers: boolean;
      refreshExplorer: boolean;
    };

export function evaluateWorkspaceEvent(
  payload: WsEventMessage,
  activeScenarioId: string | null,
): WorkspaceEventDecision {
  if (!payload.scenario_id || payload.scenario_id !== activeScenarioId) {
    return { kind: "ignore" };
  }

  const zoomRequest = parseMapZoomRequested(payload);
  if (zoomRequest) {
    return {
      kind: "zoom",
      extent: zoomRequest.extent,
      paddingPx: zoomRequest.padding_px,
      maxZoom: zoomRequest.max_zoom,
    };
  }

  const eventName = String(payload.event || "");
  if (eventName === "layer_added" || eventName === "layer_updated" || eventName === "layer_removed") {
    return {
      kind: "refresh",
      refreshLayers: true,
      refreshExplorer: false,
    };
  }
  if (eventName === "job_completed" || eventName === "job_failed" || eventName === "job_cancelled") {
    return {
      kind: "refresh",
      refreshLayers: false,
      refreshExplorer: true,
    };
  }

  return { kind: "ignore" };
}

export function shouldRefreshExplorerForAssistantEvent(event: AssistantWsEvent): boolean {
  return String(event.event || "") === "assistant_turn_completed";
}

type UseBackendEventStreamsArgs = {
  activeScenarioId: string | null;
  activeScenarioIdRef: MutableRefObject<string | null>;
  activeAssistantSessionId: string | null;
  mapControllerRef: MutableRefObject<MapController | null>;
  refreshScenarioLayers: (scenarioIdArg?: string | null) => Promise<void>;
  refreshScenarioExplorer: () => void;
  onAssistantEvent: (event: AssistantWsEvent) => void;
};

export function useBackendEventStreams(args: UseBackendEventStreamsArgs): void {
  const {
    activeScenarioId,
    activeScenarioIdRef,
    activeAssistantSessionId,
    mapControllerRef,
    refreshScenarioLayers,
    refreshScenarioExplorer,
    onAssistantEvent,
  } = args;
  const onAssistantEventRef = useRef(onAssistantEvent);

  useEffect(() => {
    onAssistantEventRef.current = onAssistantEvent;
  }, [onAssistantEvent]);

  useEffect(() => {
    if (!activeAssistantSessionId) return;
    const ws = connectAssistantEventsSocket(activeAssistantSessionId, {
      onEvent: (event) => {
        onAssistantEventRef.current(event);
        if (shouldRefreshExplorerForAssistantEvent(event)) {
          refreshScenarioExplorer();
        }
      },
      onError: () => {
        console.warn("[lunar-analyst][assistant] ws error");
      },
    });
    return () => ws.close();
  }, [activeAssistantSessionId, refreshScenarioExplorer]);

  useEffect(() => {
    if (!activeScenarioId) return;
    let refreshTimer: number | null = null;
    let refreshInFlight = false;
    let refreshQueued = false;

    const queueRefresh = (): void => {
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(() => {
        if (refreshInFlight) {
          refreshQueued = true;
          return;
        }
        refreshInFlight = true;
        void refreshScenarioLayers(activeScenarioIdRef.current).finally(() => {
          refreshInFlight = false;
          if (refreshQueued) {
            refreshQueued = false;
            queueRefresh();
          }
        });
      }, 250);
    };

    const ws = connectEventsSocket(wsEndpoint(), {
      onEvent: (payload) => {
        // Detect and warn on parse failures for map zoom requests.
        try {
          if (payload && payload.event === "map_zoom_requested") {
            const parsed = parseMapZoomRequested(payload);
            if (!parsed) {
              console.warn(
                "[lunar-analyst][ws] dropped map_zoom_requested: parse failed",
                { payload, activeScenarioId: activeScenarioIdRef.current }
              );
              // Continue so evaluateWorkspaceEvent still runs for other possible actions.
            }
          }
        } catch (err) {
          // Defensive: never throw from event handling
          // eslint-disable-next-line no-console
          console.warn("[lunar-analyst][ws] parseMapZoomRequested threw", { err, payload });
        }

        const decision = evaluateWorkspaceEvent(payload, activeScenarioIdRef.current);
        if (decision.kind === "zoom") {
          const controller = mapControllerRef.current;
          if (!controller) {
            // eslint-disable-next-line no-console
            console.warn(
              "[lunar-analyst][ws] map_zoom_requested received but map controller not ready",
              { payload, activeScenarioId: activeScenarioIdRef.current, extent: decision.extent }
            );
            return;
          }
          try {
            // Log before applying fit so we can correlate backend extents with client behavior.
            // eslint-disable-next-line no-console
            console.info("[lunar-analyst][ws] applying map_zoom_requested", { extent: decision.extent, paddingPx: decision.paddingPx, maxZoom: decision.maxZoom });
            controller.fitExtent(decision.extent, {
              paddingPx: decision.paddingPx,
              maxZoom: decision.maxZoom,
            });
            // eslint-disable-next-line no-console
            console.info("[lunar-analyst][ws] map_zoom_requested applied");
          } catch (err) {
            // eslint-disable-next-line no-console
            console.error(
              "[lunar-analyst][ws] fitExtent failed for map_zoom_requested",
              { error: err, payload, activeScenarioId: activeScenarioIdRef.current }
            );
          }
          return;
        }
        if (decision.kind === "refresh") {
          if (decision.refreshLayers) {
            queueRefresh();
          }
          if (decision.refreshExplorer) {
            refreshScenarioExplorer();
          }
        }
      },
    });

    return () => {
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
      ws.close();
    };
  }, [activeScenarioId, activeScenarioIdRef, mapControllerRef, refreshScenarioLayers, refreshScenarioExplorer]);
}
