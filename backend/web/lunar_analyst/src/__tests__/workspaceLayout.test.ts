import { describe, expect, it } from "vitest";
import {
  DEFAULT_WORKSPACE_LAYOUT_JSON,
  WORKSPACE_COMPONENTS,
  WORKSPACE_DYNAMIC_IDS,
  WORKSPACE_LAYOUT_STORAGE_KEY,
  cleanupLegacyWorkspaceLayoutKeys,
  isValidWorkspaceLayoutJson,
  loadWorkspaceLayoutJson,
} from "../layout/workspaceLayout";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.has(key) ? this.values.get(key) ?? null : null;
  }

  key(index: number): string | null {
    return Array.from(this.values.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

describe("workspaceLayout", () => {
  it("accepts the default layout model", () => {
    expect(isValidWorkspaceLayoutJson(DEFAULT_WORKSPACE_LAYOUT_JSON)).toBe(true);
  });

  it("rejects layouts with multiple map tabs", () => {
    const invalid = structuredClone(DEFAULT_WORKSPACE_LAYOUT_JSON);
    const centerRegion = invalid.layout.children[0];
    if (centerRegion.type !== "tabset") throw new Error("expected panel region");
    centerRegion.children.push({
      type: "tab",
      id: "duplicate_map",
      name: "Map 2",
      component: WORKSPACE_COMPONENTS.map,
    });
    expect(isValidWorkspaceLayoutJson(invalid)).toBe(false);
  });

  it("accepts notebook tabs in the center view group", () => {
    const valid = structuredClone(DEFAULT_WORKSPACE_LAYOUT_JSON);
    const centerRegion = valid.layout.children[0];
    if (centerRegion.type !== "tabset") throw new Error("expected panel region");
    centerRegion.children.push({
      type: "tab",
      id: `${WORKSPACE_DYNAMIC_IDS.notebookPrefix}scn_demo:terrain/example.mo.py`,
      name: "example.mo.py",
      component: WORKSPACE_COMPONENTS.notebook,
      config: {
        scenarioId: "scn_demo",
        relativePath: "terrain/example.mo.py",
        modifiedAtUtc: "2026-04-07T12:00:00Z",
      },
    });
    expect(isValidWorkspaceLayoutJson(valid)).toBe(true);
  });

  it("accepts the focused assistant workspace tab in the center view group", () => {
    const valid = structuredClone(DEFAULT_WORKSPACE_LAYOUT_JSON);
    const centerRegion = valid.layout.children[0];
    if (centerRegion.type !== "tabset") throw new Error("expected panel region");
    centerRegion.children.push({
      type: "tab",
      id: WORKSPACE_DYNAMIC_IDS.assistantWorkspace,
      name: "Assistant",
      component: WORKSPACE_COMPONENTS.assistantWorkspace,
    });
    expect(isValidWorkspaceLayoutJson(valid)).toBe(true);
  });

  it("falls back to the default layout when persisted JSON is invalid", () => {
    const storage = new MemoryStorage();
    storage.setItem(WORKSPACE_LAYOUT_STORAGE_KEY, JSON.stringify({ layout: { type: "row", children: [] } }));

    expect(loadWorkspaceLayoutJson(storage)).toEqual(DEFAULT_WORKSPACE_LAYOUT_JSON);
  });

  it("cleans up old layout schema keys", () => {
    const storage = new MemoryStorage();
    storage.setItem("lunar-analyst-workspace-layout:desktop:v0", "{}");
    storage.setItem(WORKSPACE_LAYOUT_STORAGE_KEY, JSON.stringify(DEFAULT_WORKSPACE_LAYOUT_JSON));

    cleanupLegacyWorkspaceLayoutKeys(storage);

    expect(storage.getItem("lunar-analyst-workspace-layout:desktop:v0")).toBeNull();
    expect(storage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY)).not.toBeNull();
  });
});
