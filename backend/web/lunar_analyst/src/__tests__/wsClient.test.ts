import { describe, expect, it } from "vitest";
import { parseMapZoomRequested } from "../services/wsClient";

describe("wsClient", () => {
  it("parses map_zoom_requested payloads", () => {
    const parsed = parseMapZoomRequested({
      event: "map_zoom_requested",
      scenario_id: "scn_a",
      data: {
        file_id: "file_a",
        extent: [1, 2, 3, 4],
        padding_px: 24,
        max_zoom: 10,
      },
    });
    expect(parsed).toEqual({
      scenario_id: "scn_a",
      file_id: "file_a",
      extent: [1, 2, 3, 4],
      padding_px: 24,
      max_zoom: 10,
    });
  });

  it("accepts extent-only map zoom payloads and rejects malformed ones", () => {
    expect(
      parseMapZoomRequested({
        event: "map_zoom_requested",
        scenario_id: "scn_a",
        data: { extent: [1, 2, 3, 4] },
      }),
    ).toEqual({ scenario_id: "scn_a", extent: [1, 2, 3, 4] });

    expect(
      parseMapZoomRequested({
        event: "map_zoom_requested",
        scenario_id: "scn_a",
        data: { file_id: "file_a", extent: [1, 2, 3] },
      }),
    ).toBeNull();
    expect(
      parseMapZoomRequested({
        event: "layer_added",
        scenario_id: "scn_a",
        data: { file_id: "file_a", extent: [1, 2, 3, 4] },
      }),
    ).toBeNull();
  });
});
