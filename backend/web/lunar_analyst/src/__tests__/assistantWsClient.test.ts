import { afterEach, describe, expect, it, vi } from "vitest";
import { connectAssistantEventsSocket } from "../services/assistantWsClient";

class MockWebSocket {
  public onopen: (() => void) | null = null;
  public onerror: ((event: Event) => void) | null = null;
  public onclose: ((event: CloseEvent) => void) | null = null;
  public onmessage: ((event: MessageEvent) => void) | null = null;
  public url: string;

  constructor(url: string) {
    this.url = url;
  }

  close() {
    // no-op
  }
}

describe("assistantWsClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("connects to assistant events websocket", () => {
    const ctor = vi.fn((url: string) => new MockWebSocket(url));
    vi.stubGlobal("WebSocket", ctor as unknown as typeof WebSocket);
    const received: string[] = [];
    connectAssistantEventsSocket("as_1", {
      onEvent: (event) => {
        received.push(event.event);
      },
    });
    const [url] = ctor.mock.calls[0] as [string];
    expect(url).toContain("/api/v1/assistant/sessions/as_1/events");
  });
});
