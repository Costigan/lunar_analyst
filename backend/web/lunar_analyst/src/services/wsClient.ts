export type WsEventMessage = {
  event: string;
  scenario_id?: string;
  data?: Record<string, unknown>;
};

export type MapZoomRequest = {
  scenario_id: string;
  file_id?: string;
  extent: [number, number, number, number];
  padding_px?: number;
  max_zoom?: number;
};

export type WsClientHandlers = {
  onOpen?: () => void;
  onError?: (event: Event) => void;
  onClose?: (event: CloseEvent) => void;
  onEvent: (payload: WsEventMessage) => void;
};

export function parseMapZoomRequested(payload: WsEventMessage): MapZoomRequest | null {
  if (payload.event !== "map_zoom_requested") return null;
  const scenarioId = typeof payload.scenario_id === "string" ? payload.scenario_id : "";
  const data = payload.data || {};
  const fileId = typeof data.file_id === "string" ? data.file_id : undefined;
  const rawExtent = Array.isArray(data.extent) ? data.extent : null;
  if (!scenarioId || !rawExtent || rawExtent.length !== 4) return null;
  const extent = rawExtent.map((value) => Number(value));
  if (extent.some((value) => !Number.isFinite(value))) return null;

  const mapZoomRequest: MapZoomRequest = {
    scenario_id: scenarioId,
    file_id: fileId,
    extent: [
      extent[0] as number,
      extent[1] as number,
      extent[2] as number,
      extent[3] as number,
    ],
  };
  if (typeof data.padding_px === "number" && Number.isFinite(data.padding_px)) {
    mapZoomRequest.padding_px = data.padding_px;
  }
  if (typeof data.max_zoom === "number" && Number.isFinite(data.max_zoom)) {
    mapZoomRequest.max_zoom = data.max_zoom;
  }
  return mapZoomRequest;
}

export function connectEventsSocket(endpoint: string, handlers: WsClientHandlers): WebSocket {
  const ws = new WebSocket(endpoint);
  ws.onopen = () => handlers.onOpen?.();
  ws.onerror = (event) => handlers.onError?.(event);
  ws.onclose = (event) => handlers.onClose?.(event);
  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(String(event.data || "")) as WsEventMessage;
      if (!payload || typeof payload.event !== "string") {
        return;
      }
      handlers.onEvent(payload);
    } catch {
      // Ignore malformed event payloads.
    }
  };
  return ws;
}
