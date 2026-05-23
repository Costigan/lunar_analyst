import type Projection from "ol/proj/Projection.js";
import type BaseLayer from "ol/layer/Base.js";
import TileLayer from "ol/layer/Tile.js";
import VectorLayer from "ol/layer/Vector.js";
import VectorSource from "ol/source/Vector.js";
import GeoJSON from "ol/format/GeoJSON.js";
import WKT from "ol/format/WKT.js";
import WMTSCapabilities from "ol/format/WMTSCapabilities.js";
import XYZ from "ol/source/XYZ.js";
import TileGrid from "ol/tilegrid/TileGrid.js";
import Feature from "ol/Feature.js";
import Polygon from "ol/geom/Polygon.js";
import { Fill, Stroke, Style, Circle as CircleStyle } from "ol/style.js";
import { fetchTrekLayerFeatures, type TrekLayerMetadata } from "../services/trekService";

type ParsedCapabilities = {
  Contents?: {
    Layer?: Array<{
      Identifier?: string;
      Format?: Array<string>;
      Style?: Array<{ Identifier?: string }>;
      TileMatrixSetLink?: Array<{ TileMatrixSet?: string }>;
      ResourceURL?: Array<{
        format?: string;
        resourceType?: string;
        template?: string;
      }>;
    }>;
    TileMatrixSet?: Array<{
      Identifier?: string;
      TileMatrix?: Array<{
        Identifier?: string;
        ScaleDenominator?: string | number;
        TopLeftCorner?: string | number[] | null;
        TileWidth?: string | number;
      }>;
    }>;
  };
};

type ParsedLayerInfo = NonNullable<NonNullable<ParsedCapabilities["Contents"]>["Layer"]>[number];

export function canonicalTrekLayerId(metadata: TrekLayerMetadata): string {
  const uuid = String(metadata.item_UUID || "").trim();
  if (uuid.length > 0) return uuid;
  return String(metadata.productLabel || "").trim();
}

export async function createTrekLayer(
  metadata: TrekLayerMetadata,
  projection: Projection,
): Promise<BaseLayer> {
  const featureFirst = isFeatureLikeMetadata(metadata);
  if (featureFirst) {
    try {
      return await createTrekFeatureLayer(metadata, projection);
    } catch (errorFeature) {
      console.warn("[lunar-analyst][map] trek feature load failed; trying wmts fallback", errorFeature);
      try {
        return await createTrekWmtsLayer(metadata, projection);
      } catch (errorWmts) {
        console.warn("[lunar-analyst][map] trek wmts fallback failed; trying metadata footprint", errorWmts);
        const footprintLayer = createMetadataFootprintLayer(metadata, projection);
        if (footprintLayer) return footprintLayer;
        throw errorFeature;
      }
    }
  }
  try {
    return await createTrekWmtsLayer(metadata, projection);
  } catch (errorWmtsPrimary) {
    console.warn("[lunar-analyst][map] trek wmts load failed; trying feature fallback", errorWmtsPrimary);
    try {
      return await createTrekFeatureLayer(metadata, projection);
    } catch (errorFeatureFallback) {
      console.warn("[lunar-analyst][map] trek feature fallback failed; trying metadata footprint", errorFeatureFallback);
      const footprintLayer = createMetadataFootprintLayer(metadata, projection);
      if (footprintLayer) return footprintLayer;
      throw errorWmtsPrimary;
    }
  }
}

async function createTrekWmtsLayer(metadata: TrekLayerMetadata, projection: Projection): Promise<TileLayer<XYZ>> {
  const productLabel = String(metadata.productLabel || "").trim();
  if (!productLabel) {
    throw new Error("Trek layer is missing productLabel.");
  }
  const encoded = encodeURIComponent(productLabel);
  const capabilitiesUrl = `https://trek.nasa.gov/tiles/Moon/SP/${encoded}/1.0.0/WMTSCapabilities.xml`;
  const response = await fetch(capabilitiesUrl);
  if (!response.ok) {
    throw new Error(`Trek capabilities fetch failed (${response.status}) for ${productLabel}.`);
  }
  const raw = await response.text();
  const parser = new WMTSCapabilities();
  const capabilities = parser.read(raw) as ParsedCapabilities;

  const layerInfo = (capabilities.Contents?.Layer || []).find((entry) => entry.Identifier === productLabel)
    || (capabilities.Contents?.Layer || [])[0];
  if (!layerInfo) {
    throw new Error(`No Trek WMTS layer in capabilities for ${productLabel}.`);
  }
  const matrixSet = String(layerInfo.TileMatrixSetLink?.[0]?.TileMatrixSet || "default028mm");
  const style = String(layerInfo.Style?.[0]?.Identifier || "default");
  const advertisedFormat = String(layerInfo.Format?.[0] || "image/png");
  const source = buildXyzFallback(capabilities, layerInfo, encoded, matrixSet, style, projection, advertisedFormat);
  const layer = new TileLayer({
    source,
    visible: true,
    opacity: 1,
  });
  layer.set("layer_id", canonicalTrekLayerId(metadata));
  layer.set("layer_name", String(metadata.title || productLabel));
  layer.set("layer_type", "trek_overlay");
  return layer;
}

function buildXyzFallback(
  capabilities: ParsedCapabilities,
  layerInfo: ParsedLayerInfo,
  encodedProductLabel: string,
  matrixSet: string,
  style: string,
  projection: Projection,
  format: string,
): XYZ {
  const tileMatrixSet = (capabilities.Contents?.TileMatrixSet || []).find((item) => item.Identifier === matrixSet);
  if (!tileMatrixSet || !Array.isArray(tileMatrixSet.TileMatrix) || tileMatrixSet.TileMatrix.length === 0) {
    throw new Error(`Trek fallback tile matrix set missing for ${encodedProductLabel}.`);
  }
  const resolutions = tileMatrixSet.TileMatrix.map((matrix) => Number(matrix.ScaleDenominator || 0) * 0.00028);
  const matrixIds = tileMatrixSet.TileMatrix.map((matrix) => String(matrix.Identifier || ""));
  const first = tileMatrixSet.TileMatrix[0];
  const topLeft = parseTopLeft(first.TopLeftCorner);
  const tileSize = Number(first.TileWidth || 256) || 256;
  const tileGrid = new TileGrid({
    origin: topLeft || [-1095930, 1095930],
    resolutions,
    tileSize,
  });
  const resourceTemplate = pickTileResourceTemplate(layerInfo);
  const extension = formatToExtension(format);
  return new XYZ({
    projection,
    tileGrid,
    interpolate: false,
    wrapX: false,
    crossOrigin: "anonymous",
    tileUrlFunction: (coord) => {
      if (!coord) return "";
      const z = coord[0];
      if (z < 0 || z >= matrixIds.length) return "";
      const x = coord[1];
      const y = coord[2];
      const tileMatrix = matrixIds[z];
      if (resourceTemplate) {
        return resourceTemplate
          .replace("{Style}", style)
          .replace("{TileMatrixSet}", matrixSet)
          .replace("{TileMatrix}", tileMatrix)
          .replace("{TileRow}", String(y))
          .replace("{TileCol}", String(x));
      }
      return `https://trek.nasa.gov/tiles/Moon/SP/${encodedProductLabel}/1.0.0/${style}/${matrixSet}/${tileMatrix}/${y}/${x}.${extension}`;
    },
  });
}

function pickTileResourceTemplate(
  layerInfo: ParsedLayerInfo,
): string | null {
  const resources = Array.isArray(layerInfo.ResourceURL) ? layerInfo.ResourceURL : [];
  for (const resource of resources) {
    const type = String(resource.resourceType || "").toLowerCase();
    const template = String(resource.template || "").trim();
    if (type === "tile" && template.length > 0) {
      return template;
    }
  }
  return null;
}

function formatToExtension(format: string): string {
  const normalized = String(format || "").trim().toLowerCase();
  if (normalized.includes("jpeg") || normalized.includes("jpg")) return "jpg";
  if (normalized.includes("webp")) return "webp";
  return "png";
}

function parseTopLeft(value: string | number[] | null | undefined): [number, number] | null {
  if (Array.isArray(value) && value.length >= 2) {
    const x = Number(value[0]);
    const y = Number(value[1]);
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parts = value.trim().split(/\s+/g);
    if (parts.length >= 2) {
      const x = Number(parts[0]);
      const y = Number(parts[1]);
      return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
    }
  }
  return null;
}

async function createTrekFeatureLayer(metadata: TrekLayerMetadata, projection: Projection): Promise<VectorLayer<VectorSource>> {
  const productLabel = String(metadata.productLabel || "").trim();
  if (!productLabel) {
    throw new Error("Trek feature layer is missing productLabel.");
  }
  const response = await fetchTrekLayerFeatures(productLabel);
  const payload = response.feature_collection;
  const features = new GeoJSON().readFeatures(payload, {
    dataProjection: projection,
    featureProjection: projection,
  });
  if (!features.length) {
    throw new Error(`No features returned for ${productLabel}.`);
  }
  const source = new VectorSource({
    features,
    format: new GeoJSON(),
    wrapX: false,
  });
  const firstGeometryType = features[0].getGeometry()?.getType() || "";
  const layer = new VectorLayer({
    source,
    visible: true,
    opacity: 1,
    style: buildFeatureStyleForGeometry(firstGeometryType),
  });
  layer.set("layer_id", canonicalTrekLayerId(metadata));
  layer.set("layer_name", String(metadata.title || productLabel));
  layer.set("layer_type", "trek_overlay");
  return layer;
}

function isFeatureLikeMetadata(metadata: TrekLayerMetadata): boolean {
  const serviceTypes = Array.isArray(metadata.serviceTypes)
    ? metadata.serviceTypes.map((entry) => String(entry).toLowerCase())
    : [];
  const productCat = String(metadata.productCat1 || "").toLowerCase();
  return (
    serviceTypes.some((entry) => entry.includes("feature") || entry.includes("shape") || entry.includes("vector"))
    || productCat.includes("feature")
    || productCat.includes("shape")
    || productCat.includes("vector")
  );
}

function buildFeatureStyleForGeometry(geometryType: string): Style {
  const normalized = String(geometryType || "").toLowerCase();
  if (normalized.includes("line")) {
    return new Style({
      stroke: new Stroke({
        color: "#53b5ff",
        width: 3,
      }),
    });
  }
  if (normalized.includes("point")) {
    return new Style({
      image: new CircleStyle({
        radius: 5,
        stroke: new Stroke({
          color: "#53b5ff",
          width: 2,
        }),
        fill: new Fill({ color: "rgba(83, 181, 255, 0.55)" }),
      }),
    });
  }
  const stroke = new Stroke({
    color: "#53b5ff",
    width: 2,
  });
  return new Style({
    stroke,
    fill: new Fill({ color: "rgba(83, 181, 255, 0.25)" }),
    image: new CircleStyle({
      radius: 5,
      stroke,
      fill: new Fill({ color: "rgba(83, 181, 255, 0.55)" }),
    }),
  });
}

function createMetadataFootprintLayer(
  metadata: TrekLayerMetadata,
  projection: Projection,
): VectorLayer<VectorSource> | null {
  const feature = featureFromMetadataShape(metadata, projection) || featureFromMetadataBbox(metadata);
  if (!feature) return null;

  const layer = new VectorLayer({
    source: new VectorSource({ features: [feature] }),
    visible: true,
    opacity: 1,
    style: new Style({
      stroke: new Stroke({ color: "#ffb347", width: 2 }),
      fill: new Fill({ color: "rgba(255, 179, 71, 0.18)" }),
      image: new CircleStyle({
        radius: 5,
        stroke: new Stroke({ color: "#ffb347", width: 2 }),
        fill: new Fill({ color: "rgba(255, 179, 71, 0.45)" }),
      }),
    }),
  });
  const canonical = canonicalTrekLayerId(metadata);
  layer.set("layer_id", canonical);
  layer.set("layer_name", String(metadata.title || metadata.productLabel || canonical));
  layer.set("layer_type", "trek_overlay_metadata_footprint");
  layer.set("trek_metadata_fallback", true);
  return layer;
}

function featureFromMetadataShape(metadata: TrekLayerMetadata, projection: Projection): Feature | null {
  const shape = String(metadata.shape || "").trim();
  if (!shape) return null;
  try {
    return new WKT().readFeature(shape, {
      dataProjection: projection,
      featureProjection: projection,
    });
  } catch {
    return null;
  }
}

function featureFromMetadataBbox(metadata: TrekLayerMetadata): Feature | null {
  const raw = String(metadata.trekBbox || metadata.bbox || "").trim();
  if (!raw) return null;
  const parts = raw.split(",").map((value) => Number(value.trim()));
  if (parts.length !== 4 || parts.some((value) => !Number.isFinite(value))) return null;
  const [minX, minY, maxX, maxY] = parts;
  const polygon = new Polygon([[
    [minX, minY],
    [maxX, minY],
    [maxX, maxY],
    [minX, maxY],
    [minX, minY],
  ]]);
  return new Feature({ geometry: polygon });
}
