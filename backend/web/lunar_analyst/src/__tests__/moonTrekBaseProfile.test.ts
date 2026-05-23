import { describe, expect, it } from "vitest";
import { resolveMoonTrekBaseProfile } from "../map/moonTrekBaseProfile";

describe("moonTrekBaseProfile", () => {
  it("uses deterministic default profile for the configured Moon Trek base layer", () => {
    const profile = resolveMoonTrekBaseProfile("LRO_WAC_Mosaic_SPole60_100mp", "default028mm");
    expect(profile.matrixIds).toEqual(["0", "1", "2", "3", "4"]);
    expect(profile.resolutions.length).toBe(5);
    expect(profile.resolutions[0]).toBe(8561.953125);
  });

  it("falls back to generic profile for non-default layer/matrix set", () => {
    const profile = resolveMoonTrekBaseProfile("some_other_layer", "default028mm");
    expect(profile.resolutions.length).toBe(16);
    expect(profile.matrixIds[0]).toBe("0");
    expect(profile.matrixIds[15]).toBe("15");
  });
});
