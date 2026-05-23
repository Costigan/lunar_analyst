import { describe, expect, it } from "vitest";
import { buildRasterSourceSpec, buildRasterStyle, rasterSourceSpecKey } from "../map/rasterStyle";

const COLORMAPS = {
  gray: {
    id: "gray",
    name: "Grayscale",
    stops: [
      { value: 0, color: [0, 0, 0, 1] as [number, number, number, number] },
      { value: 1, color: [255, 255, 255, 1] as [number, number, number, number] },
    ],
  },
};

describe("rasterStyle", () => {
  it("includes transparency mask for nodata and under-range when value range exists", () => {
    const style = buildRasterStyle(
      {
        source_file_id: "file-1",
        style: { valueMin: 5, valueMax: 15, nodataCutoff: -9999, colormap: "gray" },
      },
      COLORMAPS,
    );
    expect(JSON.stringify(style.color)).toContain("case");
    expect(JSON.stringify(style.color)).toContain("any");
    expect(JSON.stringify(style.color)).toContain("-9999");
    expect(JSON.stringify(style.color)).toContain("\"band\",2");
  });

  it("includes transparency mask for explicit alpha bands without nodata metadata", () => {
    const style = buildRasterStyle(
      {
        source_file_id: "file-1",
        style: { valueMin: 0, valueMax: 255, alphaBand: 2, colormap: "gray" },
      },
      COLORMAPS,
    );
    expect(JSON.stringify(style.color)).toContain("\"band\",2");
    expect(JSON.stringify(style.color)).not.toContain("-9999");
  });

  it("does not hard-clamp raw band values when no value range exists", () => {
    const style = buildRasterStyle(
      {
        source_file_id: "file-1",
        style: { colormap: "gray" },
      },
      COLORMAPS,
    );
    expect(JSON.stringify(style.color)).not.toContain("\"clamp\",[\"band\",1],0,1");
  });

  it("builds raster source spec with min/max and nodata", () => {
    expect(buildRasterSourceSpec("abc", { valueMin: 1, valueMax: 9, nodataCutoff: -1 })).toEqual({
      url: "/api/v1/lunar-analyst/files/abc/raster",
      min: 1,
      max: 9,
      nodata: -1,
    });
  });

  it("uses stable source spec key for style-driven source replacement", () => {
    expect(rasterSourceSpecKey({ valueMin: 1, valueMax: 2, nodataCutoff: -9999 })).toBe(
      JSON.stringify({ min: 1, max: 2, nodata: -9999, alphaBand: null }),
    );
  });
});
