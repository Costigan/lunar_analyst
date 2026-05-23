import { describe, expect, it } from "vitest";

import { applyRasterStatsStyle } from "../utils/rasterStatsStyle";

describe("rasterStatsStyle", () => {
  it("clears stale range/nodata/alpha style when raster stats do not expose them", () => {
    expect(
      applyRasterStatsStyle(
        { brightness: 0, valueMin: 1, valueMax: 2, nodataCutoff: 0, alphaBand: 2 },
        { min: null, max: null, nodata: null, alpha_band: null },
      ),
    ).toEqual({ brightness: 0 });
  });

  it("applies range, nodata, and alpha values from raster stats", () => {
    expect(
      applyRasterStatsStyle(
        { brightness: 0 },
        { min: 10, max: 200, nodata: -9999, alpha_band: 2 },
      ),
    ).toEqual({ brightness: 0, valueMin: 10, valueMax: 200, nodataCutoff: -9999, alphaBand: 2 });
  });
});
