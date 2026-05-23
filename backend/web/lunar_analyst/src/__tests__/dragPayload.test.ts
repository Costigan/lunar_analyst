import { describe, expect, it } from "vitest";
import { buildExplorerDragPayload, toExplorerDragPayloadJson } from "../utils/dragPayload";

describe("dragPayload", () => {
  it("preserves explorer drag/drop payload contract fields", () => {
    const payload = buildExplorerDragPayload("scenario-a", "product-b", "file-c");
    expect(payload).toEqual({
      scenario_id: "scenario-a",
      product_id: "product-b",
      file_id: "file-c",
    });
  });

  it("serializes payload for application/x-lunar-product", () => {
    const payload = buildExplorerDragPayload("sc", "prod", "file");
    expect(JSON.parse(toExplorerDragPayloadJson(payload))).toEqual(payload);
  });
});
