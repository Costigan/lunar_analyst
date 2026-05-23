export type AssistantWsEvent = {
  event: string;
  session_id?: string;
  turn_id?: string | null;
  data?: Record<string, unknown>;
};

export type AssistantWsHandlers = {
  onEvent: (event: AssistantWsEvent) => void;
  onOpen?: () => void;
  onError?: (event: Event) => void;
  onClose?: (event: CloseEvent) => void;
};

function resolveAssistantWsBaseUrl(): string {
  const locationLike =
    typeof window !== "undefined" && window.location
      ? window.location
      : typeof globalThis !== "undefined" && "location" in globalThis
        ? ((globalThis as { location?: Location }).location ?? null)
        : null;
  if (locationLike?.host) {
    const protocol = locationLike.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${locationLike.host}`;
  }
  return "ws://localhost";
}

export function connectAssistantEventsSocket(
  sessionId: string,
  handlers: AssistantWsHandlers,
): WebSocket {
  const endpoint = `${resolveAssistantWsBaseUrl()}/api/v1/assistant/sessions/${encodeURIComponent(sessionId)}/events`;
  const ws = new WebSocket(endpoint);
  ws.onopen = () => handlers.onOpen?.();
  ws.onerror = (event) => handlers.onError?.(event);
  ws.onclose = (event) => handlers.onClose?.(event);
  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(String(event.data || "")) as AssistantWsEvent;
      if (!payload || typeof payload.event !== "string") return;
      handlers.onEvent(payload);
    } catch {
      // ignore malformed payloads
    }
  };
  return ws;
}
