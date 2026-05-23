import { describe, expect, it } from "vitest";
import {
  buildLayerDiagnostics,
  pickRenderableFile,
  pickRenderableFileById,
  renderModeForFile,
  type ProductFile,
} from "../utils/layerManager";

describe("layerManager utils", () => {
  it("detects render mode from media type and extension", () => {
    expect(renderModeForFile({ file_id: "a", relative_path: "x.tif" })).toBe("raster");
    expect(renderModeForFile({ file_id: "b", relative_path: "x.geojson" })).toBe("vector");
    expect(renderModeForFile({ file_id: "c", relative_path: "x.bin" })).toBeNull();
  });

  it("picks newest renderable file and preferred file id", () => {
    const files: ProductFile[] = [
      { file_id: "1", relative_path: "a.bin", created_at_utc: "2026-01-01T00:00:00Z" },
      { file_id: "2", relative_path: "b.geojson", created_at_utc: "2026-01-02T00:00:00Z" },
      { file_id: "3", relative_path: "c.tif", created_at_utc: "2026-01-03T00:00:00Z" },
    ];

    expect(pickRenderableFile(files)?.file.file_id).toBe("3");
    expect(pickRenderableFileById(files, "2")?.renderMode).toBe("vector");
    expect(pickRenderableFileById(files, "1")).toBeNull();
  });

  it("builds raster diagnostics for range/nodata/normalization", () => {
    expect(buildLayerDiagnostics({ valueMin: 1, valueMax: 3, nodataCutoff: -9999 })).toEqual({
      range: "1.0000 .. 3.0000",
      nodata: "-9999",
      normalization: "valueMin/valueMax",
    });
    expect(buildLayerDiagnostics({})).toEqual({
      range: "auto",
      nodata: "none",
      normalization: "fallback-band-clamp",
    });
  });
});
