import { describe, expect, it } from "vitest";
import {
  getScenarioScopedList,
  updateScenarioScopedList,
  type ScenarioScopedLists,
} from "../utils/trekOverlayScopes";

describe("trekOverlayScopes", () => {
  it("returns active scenario overlays and hides overlays from other scenarios", () => {
    const byScenario: ScenarioScopedLists<string> = {
      test_scenario: ["trek_a", "trek_b"],
      lunar_analyst: ["trek_c"],
    };

    expect(getScenarioScopedList(byScenario, "test_scenario")).toEqual(["trek_a", "trek_b"]);
    expect(getScenarioScopedList(byScenario, "lunar_analyst")).toEqual(["trek_c"]);
    expect(getScenarioScopedList(byScenario, "missing")).toEqual([]);
  });

  it("updates only the targeted scenario", () => {
    const byScenario: ScenarioScopedLists<string> = {
      test_scenario: ["trek_a"],
      lunar_analyst: ["trek_c"],
    };

    const next = updateScenarioScopedList(byScenario, "test_scenario", (current) => [...current, "trek_b"]);

    expect(next).toEqual({
      test_scenario: ["trek_a", "trek_b"],
      lunar_analyst: ["trek_c"],
    });
  });

  it("drops empty scenario entries and ignores blank scenario id", () => {
    const byScenario: ScenarioScopedLists<string> = {
      test_scenario: ["trek_a"],
    };

    const removed = updateScenarioScopedList(byScenario, "test_scenario", () => []);
    expect(removed).toEqual({});

    const unchanged = updateScenarioScopedList(byScenario, "   ", (current) => [...current, "trek_b"]);
    expect(unchanged).toBe(byScenario);
  });
});
