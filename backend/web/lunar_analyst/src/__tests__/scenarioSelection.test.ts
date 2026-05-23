import { describe, expect, it } from "vitest";
import {
  persistActiveScenarioId,
  readPersistedScenarioId,
  scenarioIdFromQuery,
  selectBootstrapScenarioId,
  type StorageLike,
} from "../utils/scenarioSelection";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.has(key) ? this.values.get(key) ?? null : null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

describe("scenarioSelection", () => {
  it("reads scenario_id from the query string", () => {
    expect(scenarioIdFromQuery("?scenario_id=scn_demo")).toBe("scn_demo");
    expect(scenarioIdFromQuery("?scenario_id=%20%20")).toBeUndefined();
  });

  it("falls back to persisted storage when the URL does not specify a scenario", () => {
    const storage = new MemoryStorage();
    persistActiveScenarioId("scn_saved", storage);

    expect(selectBootstrapScenarioId({ locationSearch: "", storage })).toBe("scn_saved");
  });

  it("prefers the query string over persisted storage", () => {
    const storage = new MemoryStorage();
    persistActiveScenarioId("scn_saved", storage);

    expect(selectBootstrapScenarioId({ locationSearch: "?scenario_id=scn_url", storage })).toBe("scn_url");
  });

  it("persists and clears the active scenario id", () => {
    const storage = new MemoryStorage();

    persistActiveScenarioId(" scn_keep ", storage);
    expect(readPersistedScenarioId(storage)).toBe("scn_keep");

    persistActiveScenarioId("   ", storage);
    expect(readPersistedScenarioId(storage)).toBeUndefined();
  });
});
