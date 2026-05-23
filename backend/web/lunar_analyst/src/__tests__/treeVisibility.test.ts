import { describe, expect, it } from "vitest";
import { buildVisibleTreeRowIds, type TreeRow } from "../utils/treeVisibility";

const rows: TreeRow[] = [
  { id: "sc:a", parentId: "", name: "Scenario A", searchText: "scenario a", sortKey: "0:Scenario A" },
  { id: "node:folder", parentId: "sc:a", name: "Products", searchText: "products", sortKey: "0:Products" },
  { id: "node:file:1", parentId: "node:folder", name: "Alpha DEM", searchText: "alpha dem", sortKey: "1:Alpha DEM" },
  { id: "node:file:2", parentId: "node:folder", name: "Beta GeoJSON", searchText: "beta geojson", sortKey: "1:Beta GeoJSON" },
];

describe("treeVisibility", () => {
  it("respects expansion state when no filter", () => {
    const visible = buildVisibleTreeRowIds(rows, "", new Set(["sc:a"]));
    expect(visible).toEqual(["sc:a", "node:folder"]);
  });

  it("expands matching ancestry while filtering", () => {
    const visible = buildVisibleTreeRowIds(rows, "alp", new Set());
    expect(visible).toEqual(["sc:a", "node:folder", "node:file:1"]);
  });

  it("keeps parent context for descendant matches", () => {
    const visible = buildVisibleTreeRowIds(rows, "geo", new Set());
    expect(visible).toEqual(["sc:a", "node:folder", "node:file:2"]);
  });
});
