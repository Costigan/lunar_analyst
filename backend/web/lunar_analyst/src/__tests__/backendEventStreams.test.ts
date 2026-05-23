import { describe, expect, it } from "vitest";
import {
  evaluateWorkspaceEvent,
  shouldRefreshExplorerForAssistantEvent,
} from "../hooks/useBackendEventStreams";

describe("useBackendEventStreams", () => {
  it("ignores events for other scenarios", () => {
    const decision = evaluateWorkspaceEvent(
      { event: "layer_added", scenario_id: "scn_b", data: {} },
      "scn_a",
    );
    expect(decision).toEqual({ kind: "ignore" });
  });

  it("classifies map zoom requests", () => {
    const decision = evaluateWorkspaceEvent(
      {
        event: "map_zoom_requested",
        scenario_id: "scn_a",
        data: {
          extent: [1, 2, 3, 4],
          padding_px: 20,
          max_zoom: 7,
        },
      },
      "scn_a",
    );

    expect(decision).toEqual({
      kind: "zoom",
      extent: [1, 2, 3, 4],
      paddingPx: 20,
      maxZoom: 7,
    });
  });

  it("classifies layer updates as refresh", () => {
    const decision = evaluateWorkspaceEvent(
      { event: "layer_updated", scenario_id: "scn_a", data: {} },
      "scn_a",
    );
    expect(decision).toEqual({
      kind: "refresh",
      refreshLayers: true,
      refreshExplorer: false,
    });
  });

  it("classifies terminal job events as explorer refresh", () => {
    const decision = evaluateWorkspaceEvent(
      { event: "job_completed", scenario_id: "scn_a", data: { job_id: "job_1" } },
      "scn_a",
    );
    expect(decision).toEqual({
      kind: "refresh",
      refreshLayers: false,
      refreshExplorer: true,
    });
  });

  it("refreshes explorer when an assistant turn completes", () => {
    expect(shouldRefreshExplorerForAssistantEvent({ event: "assistant_turn_completed" })).toBe(true);
    expect(shouldRefreshExplorerForAssistantEvent({ event: "assistant_delta" })).toBe(false);
  });
});
