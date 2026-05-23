/**
 * WMTS layer creation from GetCapabilities.
 * Handles Moon Trek WMTS services.
 */

import WMTSCapabilities from 'ol/format/WMTSCapabilities';
import WMTS, { optionsFromCapabilities } from 'ol/source/WMTS';
import TileLayer from 'ol/layer/Tile';
import { get as getProjection } from 'ol/proj';
import { registerProjection } from './projection.js';

/**
 * Fetch and parse WMTS GetCapabilities document.
 * 
 * @param {string} url - GetCapabilities URL
 * @returns {Promise<Object>} Parsed capabilities object
 */
export async function fetchWMTSCapabilities(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    const text = await response.text();
    const parser = new WMTSCapabilities();
    const capabilities = parser.read(text);
    
    console.log('WMTS Capabilities loaded:', capabilities);
    return capabilities;
  } catch (error) {
    console.error('Error fetching WMTS capabilities:', error);
    throw new Error(`Failed to fetch WMTS capabilities from ${url}: ${error.message}`);
  }
}

/**
 * Create WMTS source from capabilities.
 * 
 * @param {Object} capabilities - Parsed capabilities object
 * @param {Object} config - WMTS configuration
 * @param {string} config.layer - Layer name
 * @param {string} [config.tile_matrix_set] - Tile matrix set (auto if omitted)
 * @param {string} [config.style] - Style name (auto if omitted)
 * @param {string} [config.format] - Image format (default: 'image/png')
 * @param {string} [config.attributions] - Attribution text
 * @param {import('ol/proj/Projection').default} projection - Target projection object
 * @returns {Object} WMTS source and metadata
 */
export function createWMTSSource(capabilities, config, projection) {
  try {
    const layerName = config.layer;
    
    // Find the layer in capabilities
    const layerInfo = capabilities.Contents.Layer.find(l => l.Identifier === layerName);
    if (!layerInfo) {
      const availableLayers = capabilities.Contents.Layer.map(l => l.Identifier).join(', ');
      throw new Error(`Layer "${layerName}" not found in capabilities. Available layers: ${availableLayers}`);
    }
    
    console.log('Found layer:', layerInfo);
    
    // Determine tile matrix set
    let tileMatrixSet = config.tile_matrix_set;
    if (!tileMatrixSet && layerInfo.TileMatrixSetLink && layerInfo.TileMatrixSetLink.length > 0) {
      tileMatrixSet = layerInfo.TileMatrixSetLink[0].TileMatrixSet;
      console.log('Auto-selected tile matrix set:', tileMatrixSet);
    }
    
    // Determine style
    let style = config.style;
    if (!style && layerInfo.Style && layerInfo.Style.length > 0) {
      style = layerInfo.Style[0].Identifier;
      console.log('Auto-selected style:', style);
    }
    
    // Get the tile matrix set and ensure its CRS is registered
    const tms = capabilities.Contents.TileMatrixSet.find(t => t.Identifier === tileMatrixSet);
    if (tms && tms.SupportedCRS) {
      const tmsCRS = tms.SupportedCRS;
      console.log('Tile matrix set CRS:', tmsCRS);
      
      // Check if the CRS is registered in OpenLayers
      let tmsProjection = getProjection(tmsCRS);
      
      // If not registered, try to register it if it matches our map projection
      if (!tmsProjection) {
        const projectionCode = projection.getCode();
        console.log('TMS CRS not registered, checking if it matches map projection:', projectionCode);
        
        // If the TMS CRS and map projection refer to the same thing (e.g., different notation),
        // we can use the map projection for the tile matrix set
        if (tmsCRS.includes('103878') || tmsCRS.includes('urn:ogc:def:crs:ESRI::103878')) {
          console.log('TMS CRS matches ESRI:103878, using map projection');
          // The capabilities will use the map projection we already registered
        } else {
          console.warn(`Warning: TMS CRS ${tmsCRS} is not registered and doesn't match map projection ${projectionCode}`);
        }
      }
    }
    
    // Validate projection compatibility
    const layerCRS = layerInfo.TileMatrixSetLink?.map(link => {
      const tms = capabilities.Contents.TileMatrixSet.find(t => t.Identifier === link.TileMatrixSet);
      return tms?.SupportedCRS;
    }).filter(Boolean);
    
    const projectionCode = projection.getCode();
    if (layerCRS.length > 0) {
      console.log('Layer supports CRS:', layerCRS);
      const projMatch = layerCRS.some(crs => crs.includes(projectionCode) || projectionCode.includes('103878'));
      if (!projMatch) {
        console.warn(`Warning: Layer CRS (${layerCRS.join(', ')}) may not match map projection (${projectionCode}). Client-side reprojection may be slow.`);
      }
    }
    
    // Build options for WMTS source
    console.log('Building WMTS options with:', {
      layer: layerName,
      matrixSet: tileMatrixSet,
      style: style,
      format: config.format || 'image/png',
      projection: projection.getCode()
    });
    
    // Important: Use the native WMTS projection (urn:ogc:def:crs:EPSG::0) which we've registered as equivalent to ESRI:103878
    // This allows OpenLayers to properly construct tile URLs for the WMTS service
    const wmtsProjection = getProjection('urn:ogc:def:crs:EPSG::0');
    
    if (!wmtsProjection) {
      throw new Error('WMTS projection urn:ogc:def:crs:EPSG::0 not found. Make sure projections are registered.');
    }
    
    let options = optionsFromCapabilities(capabilities, {
      layer: layerName,
      matrixSet: tileMatrixSet,
      style: style,
      format: config.format || 'image/png',
      projection: wmtsProjection  // Use the WMTS's native projection
    });
    
    console.log('WMTS options result:', options);
    
    if (!options) {
      throw new Error(`Failed to create WMTS options for layer ${layerName}`);
    }
    
    // Fix tile grid extent if it contains NaN values
    // This can happen with certain WMTS services (like Moon Trek) where OpenLayers
    // has trouble calculating the full extent from the tile matrix definition
    if (options.tileGrid) {
      const gridExtent = options.tileGrid.getExtent();
      
      if (gridExtent && (isNaN(gridExtent[1]) || isNaN(gridExtent[2]))) {
        console.warn('Tile grid has invalid extent, reconstructing with valid bounds');
        
        // For polar stereographic projections, the extent should be square and symmetric
        const maxExtent = Math.abs(gridExtent[0]);
        const fixedExtent = [-maxExtent, -maxExtent, maxExtent, maxExtent];
        
        // Create a new tile grid with the fixed extent
        const WMTSTileGrid = options.tileGrid.constructor;
        options.tileGrid = new WMTSTileGrid({
          origin: options.tileGrid.getOrigin(0),
          resolutions: options.tileGrid.getResolutions(),
          matrixIds: options.tileGrid.getMatrixIds(),
          extent: fixedExtent,
          tileSize: options.tileGrid.getTileSize(0)
        });
      }
    }
    
    // Add attribution if provided
    if (config.attributions) {
      options.attributions = config.attributions;
    }
    
    // Enable CORS for export functionality
    options.crossOrigin = 'anonymous';
    
    console.log('Creating WMTS source with options:', options);
    
    // Create the WMTS source
    const source = new WMTS(options);
    
    // Log tile errors for debugging
    source.on('tileloaderror', (event) => {
      console.error('Tile load error:', event.tile.getTileCoord(), event);
    });
    
    // Extract extent from layer bounding box
    let extent = null;
    
    if (layerInfo.BoundingBox && layerInfo.BoundingBox.length > 0) {
      const bbox = layerInfo.BoundingBox[0];
      
      // OpenLayers parses BoundingBox into {extent: [minX, minY, maxX, maxY], crs: ...}
      if (bbox.extent && Array.isArray(bbox.extent) && bbox.extent.length === 4) {
        extent = bbox.extent;
      } else if (bbox.LowerCorner && bbox.UpperCorner) {
        // Fallback for older format
        extent = [
          bbox.LowerCorner[0],
          bbox.LowerCorner[1],
          bbox.UpperCorner[0],
          bbox.UpperCorner[1]
        ];
        console.log('WMTS extent from layer BoundingBox corners:', extent);
      }
    }
    
    // Fallback: try tile matrix set bounding box
    if (!extent && tms && tms.BoundingBox) {
      extent = tms.BoundingBox.LowerCorner.concat(tms.BoundingBox.UpperCorner);
      console.log('WMTS extent from TileMatrixSet:', extent);
    }
    
    // Last resort: use a default extent for south polar region
    if (!extent) {
      extent = [-136716, -136875, 136863, 136746];
      console.log('Using hardcoded fallback extent for south polar region:', extent);
    }
    
    return {
      source,
      extent,
      layerInfo,
      tileMatrixSet,
      style
    };
  } catch (error) {
    console.error('Error creating WMTS source:', error);
    throw error;
  }
}

/**
 * Create WMTS tile layer from GetCapabilities URL.
 * 
 * @param {Object} config - WMTS configuration
 * @param {string} config.get_capabilities_url - GetCapabilities URL
 * @param {string} config.layer - Layer name
 * @param {string} [config.tile_matrix_set] - Tile matrix set
 * @param {string} [config.style] - Style name
 * @param {string} [config.format] - Image format
 * @param {string} [config.attributions] - Attribution text
 * @param {import('ol/proj/Projection').default} projection - Target projection object
 * @returns {Promise<Object>} Tile layer and metadata
 */
export async function createWMTSLayer(config, projection) {
  try {
    if (!config || !config.get_capabilities_url) {
      throw new Error('WMTS configuration requires get_capabilities_url');
    }
    
    if (!config.layer) {
      throw new Error('WMTS configuration requires layer name');
    }
    
    console.log('Creating WMTS layer:', config);
    
    // Fetch capabilities
    const capabilities = await fetchWMTSCapabilities(config.get_capabilities_url);
    
    // Create source
    const { source, extent, layerInfo, tileMatrixSet, style } = createWMTSSource(
      capabilities,
      config,
      projection
    );
    
    // Create tile layer
    const layer = new TileLayer({
      source: source,
      opacity: 1.0,
      visible: true,
      title: config.layer  // For layer switcher
    });
    
    // Set layer properties
    layer.set('layer_id', 'wmts_base');
    layer.set('layer_type', 'wmts');
    layer.set('layer_name', config.layer);
    layer.set('title', config.layer);  // For layer switcher
    layer.set('extent', extent);
    
    console.log('WMTS layer created successfully');
    
    return {
      layer,
      extent,
      capabilities,
      layerInfo,
      tileMatrixSet,
      style
    };
  } catch (error) {
    console.error('Error creating WMTS layer:', error);
    throw error;
  }
}
