import { GeoTIFFImage } from "geotiff";

const DEFERRED_GDAL_NODATA_MESSAGE = /Field 'GDAL_NODATA' \(42113\) is deferred/i;

type GeoTIFFImageCtor = {
  prototype: {
    getGDALNoData(this: unknown): number | null;
    __lunarAnalystDeferredGdalNodataPatched__?: boolean;
  };
};

let warnedAboutDeferredGdalNodata = false;

export function patchDeferredGdalNodataAccessor(ImageCtor: GeoTIFFImageCtor): void {
  const proto = ImageCtor.prototype;
  if (proto.__lunarAnalystDeferredGdalNodataPatched__) {
    return;
  }

  const original = proto.getGDALNoData;
  proto.getGDALNoData = function patchedGetGDALNoData(this: unknown): number | null {
    try {
      return original.call(this);
    } catch (error) {
      if (error instanceof Error && DEFERRED_GDAL_NODATA_MESSAGE.test(error.message)) {
        if (!warnedAboutDeferredGdalNodata) {
          warnedAboutDeferredGdalNodata = true;
          console.warn(
            "[lunar-analyst][map] deferred GDAL_NODATA metadata encountered; continuing without synchronous file nodata metadata",
          );
        }
        return null;
      }
      throw error;
    }
  };
  proto.__lunarAnalystDeferredGdalNodataPatched__ = true;
}

export function installDeferredGdalNodataPatch(): void {
  patchDeferredGdalNodataAccessor(GeoTIFFImage as unknown as GeoTIFFImageCtor);
}
