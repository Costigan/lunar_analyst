import { describe, expect, it } from "vitest";
import {
  DEFAULT_MOON_TREK_TILE_BASE_URL,
  resolveMoonTrekTileBaseUrl,
} from "../map/moonTrekUrl";

describe("moonTrekUrl", () => {
  it("returns default base url when config is missing", () => {
    expect(resolveMoonTrekTileBaseUrl("")).toBe(DEFAULT_MOON_TREK_TILE_BASE_URL);
  });

  it("derives base tile path from capabilities url", () => {
    const base = resolveMoonTrekTileBaseUrl(
      "https://example.test/tiles/Moon/SP/LRO_WAC_Mosaic_SPole60_100mp/1.0.0/WMTSCapabilities.xml",
    );
    expect(base).toBe("https://example.test/tiles/Moon/SP");
  });

  it("falls back to default when url is malformed", () => {
    expect(resolveMoonTrekTileBaseUrl("not-a-url")).toBe(DEFAULT_MOON_TREK_TILE_BASE_URL);
  });
});
