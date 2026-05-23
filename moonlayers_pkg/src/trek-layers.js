/**
 * Factory for creating layers from NASA Trek API metadata.
 */

import TileLayer from 'ol/layer/Tile';
import WebGLTileLayer from 'ol/layer/WebGLTile';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import GeoJSON from 'ol/format/GeoJSON';
import { bbox as bboxStrategy } from 'ol/loadingstrategy';
import { Stroke, Style, Fill, Circle as CircleStyle } from 'ol/style';
import WMTS from 'ol/source/WMTS';
import WMTSTileGrid from 'ol/tilegrid/WMTS';
import GeoTIFF from 'ol/source/GeoTIFF';
import { get as getProjection } from 'ol/proj';
import { getTopLeft, getWidth } from 'ol/extent';

/**
 * Create a WMTS layer from Trek metadata by fetching GetCapabilities.
 * 
 * @param {Object} metadata - Trek layer metadata
 * @param {import('ol/proj/Projection').default} projection - Map projection
 * @returns {Promise<import('ol/layer/Tile').default>} WMTS layer
 */
export async function createTrekWMTSLayer(metadata, projection) {
  const productLabel = metadata.productLabel;
  const layerId = metadata.item_UUID;
  const title = metadata.title || productLabel;
  
  console.log('Creating Trek WMTS layer:', productLabel);
  
  // Import wmts utilities
  const { createWMTSLayer } = await import('./wmts.js');
  
  // Construct GetCapabilities URL
  const capabilitiesUrl = `https://trek.nasa.gov/tiles/Moon/SP/${productLabel}/1.0.0/WMTSCapabilities.xml`;
  
  console.log('Fetching Trek WMTS GetCapabilities:', capabilitiesUrl);
  
  try {
    // Use the same WMTS layer creation as the base layer
    const result = await createWMTSLayer({
      get_capabilities_url: capabilitiesUrl,
      layer: productLabel,
      format: 'image/png',
      tile_matrix_set: 'default028mm',
      attributions: metadata.description || title
    }, projection);
    
    const layer = result.layer;
    
    // Set layer properties for management
    layer.set('layer_id', layerId);
    layer.set('layer_type', 'trek_wmts');
    layer.set('layer_name', title);
    layer.set('title', title);
    layer.set('trek_metadata', metadata);
    
    console.log('Trek WMTS layer created:', layerId, title);
    
    return layer;
    
  } catch (error) {
    console.error('Failed to create Trek WMTS layer:', error);
    throw new Error(`Failed to load Trek layer ${productLabel}: ${error.message}`);
  }
}

/**
 * Create a vector/feature layer from Trek metadata.
 * 
 * @param {Object} metadata - Trek layer metadata
 * @param {import('ol/proj/Projection').default} projection - Map projection
 * @returns {Promise<import('ol/layer/Vector').default>} Vector layer
 */
export async function createTrekFeatureLayer(metadata, projection) {
  const productLabel = metadata.productLabel;
  const layerId = metadata.item_UUID;
  const title = metadata.title || productLabel;
  
  console.log('Creating Trek Feature layer:', productLabel);
  
  // Construct ArcGIS MapServer URL
  const mapServerUrl = `https://trek.nasa.gov/moon/trekarcgis2/rest/services/${productLabel}/MapServer/0`;
  
  try {
    // Fetch layer info to get metadata
    const infoUrl = `${mapServerUrl}?f=json`;
    console.log('Fetching feature layer info:', infoUrl);
    
    const infoResponse = await fetch(infoUrl);
    if (!infoResponse.ok) {
      const errorText = await infoResponse.text();
      console.error('MapServer response error:', infoResponse.status, errorText);
      throw new Error(`Failed to fetch layer info: ${infoResponse.statusText}`);
    }
    
    const layerInfo = await infoResponse.json();
    
    // Check if the response indicates an error
    if (layerInfo.error) {
      console.error('MapServer returned error:', layerInfo.error);
      throw new Error(`MapServer error: ${layerInfo.error.message || layerInfo.error.code}`);
    }
    
    console.log('Feature layer info:', layerInfo);
    
    // Get spatial reference code
    const spatialRef = layerInfo.extent?.spatialReference?.wkid || 
                      layerInfo.spatialReference?.wkid ||
                      projection.getCode().split(':')[1];
    
    // Create vector source with URL loader for bbox-based loading
    const source = new VectorSource({
      format: new GeoJSON(),
      loader: async function(extent, resolution, projection) {
        const queryUrl = `${mapServerUrl}/query`;
        
        // Build query parameters
        const params = new URLSearchParams({
          geometry: extent.join(','),
          geometryType: 'esriGeometryEnvelope',
          spatialRel: 'esriSpatialRelIntersects',
          outFields: '*',
          returnGeometry: 'true',
          f: 'geojson',
          outSR: spatialRef
        });
        
        const fullUrl = `${queryUrl}?${params}`;
        console.log('Fetching features:', fullUrl);
        
        try {
          const response = await fetch(fullUrl);
          if (!response.ok) {
            console.error(`Failed to fetch features: ${response.statusText}`);
            return;
          }
          
          const geojson = await response.json();
          
          // Check for ArcGIS error response
          if (geojson.error) {
            console.error('Feature query error:', geojson.error);
            return;
          }
          
          console.log('Received features:', geojson.features?.length || 0);
          
          // Read features and add to source
          const features = new GeoJSON().readFeatures(geojson, {
            dataProjection: projection,
            featureProjection: projection
          });
          
          source.addFeatures(features);
          
        } catch (error) {
          console.error('Error loading features:', error);
        }
      },
      strategy: bboxStrategy,
      wrapX: false
    });
    
    // Create style based on geometry type
    const geometryType = layerInfo.geometryType || 'esriGeometryPolygon';
    const style = createFeatureStyle(geometryType);
    
    // Create vector layer
    const layer = new VectorLayer({
      source: source,
      style: style,
      opacity: 0.7,
      visible: true
    });
    
    // Set layer properties for management
    layer.set('layer_id', layerId);
    layer.set('layer_type', 'trek_feature');
    layer.set('layer_name', title);
    layer.set('title', title);
    layer.set('trek_metadata', metadata);
    
    console.log('Trek Feature layer created:', layerId, title);
    
    return layer;
    
  } catch (error) {
    console.error('Failed to create Trek Feature layer:', error);
    
    // Provide more helpful error message
    let errorMsg = `Failed to load Trek feature layer ${productLabel}: ${error.message}`;
    
    if (error.message.includes('503') || error.message.includes('Service Unavailable')) {
      errorMsg += '\n\nThe Trek MapServer appears to be temporarily unavailable. This is a server-side issue. You can try again later or use a different layer.';
    } else if (error.message.includes('404') || error.message.includes('Not Found')) {
      errorMsg += '\n\nThis feature layer may not be available as a MapServer service. It might only be available as a file download.';
    }
    
    throw new Error(errorMsg);
  }
}

/**
 * Create style based on geometry type.
 * @private
 */
function createFeatureStyle(geometryType) {
  const stroke = new Stroke({
    color: '#3399CC',
    width: 2
  });
  
  const fill = new Fill({
    color: 'rgba(51, 153, 204, 0.3)'
  });
  
  if (geometryType === 'esriGeometryPoint' || geometryType === 'Point') {
    return new Style({
      image: new CircleStyle({
        radius: 6,
        fill: new Fill({ color: 'rgba(51, 153, 204, 0.6)' }),
        stroke: stroke
      })
    });
  } else if (geometryType === 'esriGeometryPolyline' || geometryType === 'LineString') {
    return new Style({
      stroke: new Stroke({
        color: '#3399CC',
        width: 3
      })
    });
  } else {
    // Polygon or default
    return new Style({
      stroke: stroke,
      fill: fill
    });
  }
}

/**
 * Create a GeoTIFF/raster layer from Trek metadata.
 * 
 * @param {Object} metadata - Trek layer metadata
 * @param {import('ol/proj/Projection').default} projection - Map projection
 * @returns {Promise<import('ol/layer/WebGLTile').default>} GeoTIFF layer
 */
export async function createTrekGeoTIFFLayer(metadata, projection) {
  const productLabel = metadata.productLabel;
  const layerId = metadata.item_UUID;
  const title = metadata.title || productLabel;
  
  console.log('Creating Trek GeoTIFF layer:', productLabel);
  
  // Construct potential GeoTIFF URLs - Trek may serve them from different locations
  const potentialUrls = [
    `https://trek.nasa.gov/tiles/Moon/SP/${productLabel}/data.tif`,
    `https://trek.nasa.gov/moon/pds/GeoTiffProducts/${productLabel}.tif`,
    `https://trek.nasa.gov/tiles/Moon/${productLabel}.tif`
  ];
  
  // Try to find a working GeoTIFF URL
  let geotiffUrl = null;
  for (const url of potentialUrls) {
    try {
      const response = await fetch(url, { method: 'HEAD' });
      if (response.ok) {
        geotiffUrl = url;
        console.log('Found GeoTIFF at:', url);
        break;
      }
    } catch (error) {
      // Try next URL
      continue;
    }
  }
  
  if (!geotiffUrl) {
    throw new Error(`Could not locate GeoTIFF file for ${productLabel}. Tried: ${potentialUrls.join(', ')}`);
  }
  
  try {
    // Create GeoTIFF source with COG support
    const source = new GeoTIFF({
      sources: [
        {
          url: geotiffUrl,
          // Will auto-detect bands from the file
        }
      ],
      // Enable efficient tiled loading for pyramided TIFFs
      transition: 0,
      interpolate: true,
      normalize: false,
      wrapX: false
    });
    
    // Create WebGL tile layer for efficient rendering
    const layer = new WebGLTileLayer({
      source: source,
      opacity: 1.0,
      visible: true
    });
    
    // Set layer properties for management
    layer.set('layer_id', layerId);
    layer.set('layer_type', 'trek_geotiff');
    layer.set('layer_name', title);
    layer.set('title', title);
    layer.set('trek_metadata', metadata);
    
    console.log('Trek GeoTIFF layer created:', layerId, title);
    
    return layer;
    
  } catch (error) {
    console.error('Failed to create Trek GeoTIFF layer:', error);
    throw new Error(`Failed to load Trek GeoTIFF layer ${productLabel}: ${error.message}`);
  }
}

/**
 * Create an appropriate layer from Trek metadata based on serviceTypes or productCat1.
 * 
 * @param {Object} metadata - Trek layer metadata
 * @param {import('ol/proj/Projection').default} projection - Map projection
 * @returns {Promise<import('ol/layer/Base').default>} OpenLayers layer
 */
export async function createTrekLayer(metadata, projection) {
  const serviceTypes = metadata.serviceTypes || [];
  const productCat1 = metadata.productCat1 || '';
  
  console.log('Creating Trek layer:', {
    productLabel: metadata.productLabel,
    serviceTypes: serviceTypes,
    productCat1: productCat1
  });
  
  // Check for Feature (vector) layers first - can be in serviceTypes OR productCat1
  if (serviceTypes.includes('Feature') || productCat1 === 'Feature') {
    return await createTrekFeatureLayer(metadata, projection);
  }
  
  // Check for Mosaic (WMTS) layers - most common
  if (serviceTypes.includes('Mosaic')) {
    return await createTrekWMTSLayer(metadata, projection);
  }
  
  // Check for Raster/GeoTIFF layers
  if (serviceTypes.includes('Raster') || serviceTypes.includes('GeoTIFF')) {
    return await createTrekGeoTIFFLayer(metadata, projection);
  }
  
  // Default to WMTS if no specific type is identified
  // Many Trek layers don't have explicit serviceTypes but are WMTS
  console.log('No explicit service type found, attempting WMTS...');
  return await createTrekWMTSLayer(metadata, projection);
}
