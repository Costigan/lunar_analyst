import { describe, expect, it } from "vitest";
import { computeDropIndexForRow, planLayerReorderZPatches, type LayerState } from "../utils/layerOrder";

describe("layerOrder", () => {
  it("computes row-assisted drop index for downward moves", () => {
    const stack = new Map([
      ["a", 0],
      ["b", 1],
      ["c", 2],
    ]);
    expect(computeDropIndexForRow(2, "a", stack)).toBe(3);
    expect(computeDropIndexForRow(0, "c", stack)).toBe(0);
    expect(computeDropIndexForRow(1, "b", stack)).toBe(1);
  });

  it("plans z-index patches around base layer", () => {
    const layers: LayerState[] = [
      { layer_id: "a", z_index: 30 },
      { layer_id: "b", z_index: 20 },
      { layer_id: "c", z_index: 10 },
    ];
    const patches = planLayerReorderZPatches(layers, "c", 0, 0);
    expect(patches).toEqual([
      { layer_id: "b", z_index: 10 },
      { layer_id: "a", z_index: 20 },
      { layer_id: "c", z_index: 30 },
    ]);
  });

  it("plans z-index patches when no base layer entry exists", () => {
    const layers: LayerState[] = [
      { layer_id: "a", z_index: 10 },
      { layer_id: "b", z_index: 20 },
      { layer_id: "c", z_index: 30 },
    ];
    const patches = planLayerReorderZPatches(layers, "a", 0, null);
    expect(patches).toEqual([
      { layer_id: "b", z_index: 10 },
      { layer_id: "c", z_index: 20 },
      { layer_id: "a", z_index: 30 },
    ]);
  });
});
