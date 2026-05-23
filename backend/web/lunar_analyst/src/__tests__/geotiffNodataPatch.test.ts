import { describe, expect, it, vi } from "vitest";

import { patchDeferredGdalNodataAccessor } from "../map/geotiffNodataPatch";

class FakeGeoTIFFImage {
  public getGDALNoData(): number | null {
    return null;
  }
}

describe("geotiffNodataPatch", () => {
  it("returns null instead of throwing for deferred GDAL_NODATA access", () => {
    class DeferredGeoTIFFImage extends FakeGeoTIFFImage {
      public override getGDALNoData(): number | null {
        throw new Error("Field 'GDAL_NODATA' (42113) is deferred. Use loadValue() to load it asynchronously.");
      }
    }

    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    patchDeferredGdalNodataAccessor(DeferredGeoTIFFImage as unknown as typeof FakeGeoTIFFImage);

    expect(new DeferredGeoTIFFImage().getGDALNoData()).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });

  it("rethrows unrelated errors", () => {
    class BrokenGeoTIFFImage extends FakeGeoTIFFImage {
      public override getGDALNoData(): number | null {
        throw new Error("some other failure");
      }
    }

    patchDeferredGdalNodataAccessor(BrokenGeoTIFFImage as unknown as typeof FakeGeoTIFFImage);
    expect(() => new BrokenGeoTIFFImage().getGDALNoData()).toThrowError("some other failure");
  });
});
