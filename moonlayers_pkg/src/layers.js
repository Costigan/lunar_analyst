
import GeoTIFF from 'ol/source/GeoTIFF';
import ImageStatic from 'ol/source/ImageStatic';
import VectorSource from 'ol/source/Vector';
import GeoJSON from 'ol/format/GeoJSON';
import { Tile as TileLayer, WebGLTile as WebGLTileLayer, Image as ImageLayer, Vector as VectorLayer } from 'ol/layer';
import { Style, Stroke, Fill, Circle as CircleStyle, Icon, Text } from 'ol/style';
import { transformExtent } from 'ol/proj';
import { get as getProjection, addEquivalentProjections } from 'ol/proj';
import { registerProjection } from './projection.js';
import proj4 from 'proj4';
import { register } from 'ol/proj/proj4';

/**
 * Convert a data URL to a Blob URL that properly supports range requests.
 * Creates a proper Blob object that can be accessed multiple times.
 * 
 * @param {string} dataUrl - Data URL to convert
 * @returns {string} Blob URL
 */
function dataUrlToBlobUrl(dataUrl) {
  // Extract mime type and base64 data
  const match = dataUrl.match(/^data:([^;]+);base64,(.+)$/);
  if (!match) {
    // Not a base64 data URL, return as-is
    return dataUrl;
  }
  
  const mimeType = match[1];
  const base64Data = match[2];
  
  // Decode base64 to binary
  const binaryString = atob(base64Data);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  
  // Create blob and return blob URL
  const blob = new Blob([bytes], { type: mimeType });
  const blobUrl = URL.createObjectURL(blob);
  console.log(`Converted data URL (${Math.round(base64Data.length / 1024)} KB) to Blob URL (${Math.round(len / 1024)} KB)`);
  return blobUrl;
}

// === MoonLayers JS Build Info ===
console.log('MoonLayers JS version: 2025-10-09 build 4 (version in src/layers.js');
// ===============================

/**
 * Generate WebGL style for GeoTIFF rendering based on band count and data type.
 * 
 * @param {Object} sourceView - GeoTIFF source view metadata
 * @param {Object} styleConfig - Optional custom style configuration
 * @returns {Object} WebGL style object
 */
function createGeoTIFFStyle(sourceView, styleConfig = {}) {
  const bandCount = sourceView.bands ? sourceView.bands.length : 1;
  
  console.log(`Creating GeoTIFF style for ${bandCount} band(s)`);
  
  // If custom style provided, use it
  if (styleConfig.color) {
    console.log('Using custom color style from config');
    return { color: styleConfig.color };
  }
  
  // Auto-detect style based on band count
  if (bandCount === 1) {
    // Single band: grayscale with nodata handling
    const nodata = styleConfig.nodata !== undefined ? styleConfig.nodata : 0;
    const min = styleConfig.min !== undefined ? styleConfig.min : 0;
    const max = styleConfig.max !== undefined ? styleConfig.max : 255;
    const range = max - min;
    
    console.log(`Single-band grayscale: min=${min}, max=${max}, nodata=${nodata}`);
    
    return {
      color: [
        'case',
        ['==', ['band', 1], nodata],
        [0, 0, 0, 0], // Transparent for nodata
        [
          'array',
          ['/', ['-', ['band', 1], min], range], // Normalize to 0-1
          ['/', ['-', ['band', 1], min], range],
          ['/', ['-', ['band', 1], min], range],
          1
        ]
      ]
    };
  } else if (bandCount === 3) {
    // Three bands: RGB
    const min = styleConfig.min !== undefined ? styleConfig.min : 0;
    const max = styleConfig.max !== undefined ? styleConfig.max : 255;
    const range = max - min;
    
    console.log(`RGB: min=${min}, max=${max}`);
    
    return {
      color: [
        'array',
        ['/', ['-', ['band', 1], min], range], // R
        ['/', ['-', ['band', 2], min], range], // G
        ['/', ['-', ['band', 3], min], range], // B
        1                                       // A
      ]
    };
  } else if (bandCount === 4) {
    // Four bands: RGBA
    const min = styleConfig.min !== undefined ? styleConfig.min : 0;
    const max = styleConfig.max !== undefined ? styleConfig.max : 255;
    const range = max - min;
    
    console.log(`RGBA: min=${min}, max=${max}`);
    
    return {
      color: [
        'array',
        ['/', ['-', ['band', 1], min], range], // R
        ['/', ['-', ['band', 2], min], range], // G
        ['/', ['-', ['band', 3], min], range], // B
        ['/', ['band', 4], 255]                 // A (0-255)
      ]
    };
  } else {
    // Fallback: just use first band as grayscale
    console.warn(`Unsupported band count (${bandCount}), falling back to first band`);
    return {
      color: ['band', 1]
    };
  }
}

/**
 * Create a GeoTIFF layer.
 * 
 * @param {Object} config - GeoTIFF configuration
 * @param {string} config.url - URL to GeoTIFF file (COG preferred)
 * @param {string} config.layer_id - Unique layer identifier
 * @param {number} [config.opacity=1.0] - Layer opacity (0-1)
 * @param {boolean} [config.visible=true] - Initial visibility
 * @param {import('ol/proj/Projection').default} projection - Map projection object
 * @returns {Promise<Object>} Layer and metadata
 */
export async function createGeoTIFFLayer(config, projection) {
  try {
    if (!config.url) {
      throw new Error('GeoTIFF configuration requires url');
    }
    
    const layerId = config.layer_id || `geotiff_${Date.now()}`;
    
    // Convert data URLs to Blob URLs for better handling
    let url = config.url;
    if (config.url.startsWith('data:')) {
      url = dataUrlToBlobUrl(config.url);
    }
    
    console.log('Creating GeoTIFF layer:', layerId, url.substring(0, 100) + '...');
    
    // Python side ensures all GeoTIFFs are in ESRI:103878 projection
    // Either by metadata rewrite (if equivalent) or reprojection (if different)
    // So we can use GeoTIFF source directly without custom projection handling
    
  /**
   * MoonLayers JS Build Info
   * version: 2025-10-09T14:45:00Z build v8 (Blob URL simple), src/layers.js patch active
   */
  console.log('MoonLayers JS version: 2025-10-09T14:45:00Z build v8 (Blob URL simple), src/layers.js patch active');
    
    // Build source configuration
    const sourceConfig = {
      sources: [{
        url: url,
        // Enable CORS for export
        crossOrigin: 'anonymous',
        // Explicitly set projection - Python normalizes all GeoTIFFs to ESRI:103878
        // This overrides any projection read from the file
        projection: projection.getCode()
      }],
      // Don't convert to RGB - let WebGL style handle the rendering
      convertToRGB: false,
      // Don't normalize pixel values - we handle normalization in the style
      normalize: false
    };
    
    // If extent is provided, add it to source config
    if (config.extent) {
      sourceConfig.sources[0].extent = config.extent;
      console.log('Using provided extent:', config.extent);
    }
    
    // Create GeoTIFF source
    const source = new GeoTIFF(sourceConfig);
    
    // Add error handler for the source
    source.on('error', (error) => {
      console.error('GeoTIFF source error event:', error);
      console.error('Error details:', {
        message: error.message,
        stack: error.stack,
        sourceConfig: JSON.stringify(sourceConfig, null, 2)
      });
    });
    
    // Wait for the source to be ready before creating the layer
    await new Promise((resolve, reject) => {
      const checkReady = () => {
        const state = source.getState();
        console.log('GeoTIFF source state:', state);
        
        if (state === 'ready') {
          console.log('GeoTIFF source is ready');
          resolve();
        } else if (state === 'error') {
          const error = new Error('GeoTIFF source failed to load');
          console.error('GeoTIFF source entered error state');
          console.error('Check console for GeoTIFF source error event details above');
          reject(error);
        }
      };
      
      source.on('change', checkReady);
      checkReady(); // Check immediately in case already ready
      
      // Timeout after 30 seconds
      setTimeout(() => {
        console.error('GeoTIFF source loading timeout after 30 seconds');
        reject(new Error('GeoTIFF source loading timeout'));
      }, 30000);
    });
    
    // Get view information from the loaded source (it's a Promise!)
    const sourceView = await source.getView();
    console.log('GeoTIFF source view:', sourceView);
    console.log('GeoTIFF source projection:', sourceView.projection ? sourceView.projection.getCode() : 'null');
    
    // Patch band count if config provides it (fixes issue where stripped TIFFs report 1 band)
    if (config.bands && config.bands > 0) {
      const detectedBands = sourceView.bands ? sourceView.bands.length : 0;
      if (detectedBands < config.bands) {
        console.log(`Overriding detected bands (${detectedBands}) with config bands (${config.bands})`);
        // Mock bands array - createGeoTIFFStyle only checks length
        sourceView.bands = Array(config.bands).fill({});
      }
    }
    
    // Debug: Try to extract WKT from the GeoTIFF metadata
    try {
      const image = await source.getGeoKeys();
      console.log('GeoTIFF GeoKeys:', image);
    } catch (e) {
      console.log('Could not read GeoTIFF GeoKeys:', e);
    }
    
    // Fallback: Use projection from layer config if GeoTIFF metadata is not recognized
    let effectiveProjection = sourceView.projection;
    if (!effectiveProjection && config.projection) {
      console.log('GeoTIFF source has no projection, using config projection:', config.projection);
      effectiveProjection = getProjection(config.projection);
      console.log('Resolved projection from config:', effectiveProjection ? effectiveProjection.getCode() : 'null');
    }
    
    // The projection should now be recognized since Python normalized to ESRI:103878
    if (!effectiveProjection) {
      console.error('GeoTIFF source still has no projection after normalization and fallback!');
      console.error('Config:', config);
      throw new Error(
        `GeoTIFF source has no projection information after normalization. Layer: ${layerId}`
      );
    }
    
    // If the GeoTIFF projection object differs from the map's projection,
    // mark them as equivalent to avoid unnecessary reprojection triangulation
    // that can manifest as transient triangular holes.
    try {
      const effCode = effectiveProjection.getCode?.();
      const mapCode = projection.getCode?.();
      if (!effCode || !mapCode || effCode !== mapCode) {
        addEquivalentProjections([effectiveProjection, projection]);
        console.log('Marked GeoTIFF projection as equivalent to map projection:', {
          geoTiffProj: effCode || '<no-code>',
          mapProj: mapCode || '<no-code>'
        });
      }
    } catch (e) {
      console.warn('Could not add equivalent projections for GeoTIFF/map:', e);
    }
    
    // Create WebGLTile layer for GeoTIFF sources (they return DataTiles, not ImageTiles)
    // WebGLTileLayer handles DataTile rendering properly
    // Generate style based on band count and configuration
    const webglStyle = createGeoTIFFStyle(sourceView, config.style || {});
    
    const layer = new WebGLTileLayer({
      source: source,
      opacity: config.opacity !== undefined ? config.opacity : 1.0,
      visible: config.visible !== undefined ? config.visible : true,
      // Avoid hard clipping by extent during render; we still store the extent on the layer
      // so consumers can fit to it. Hard clipping here can create visual artifacts if slightly off.
      style: webglStyle,
      transition: 0,
      wrapX: false
    });
    
    // Set layer properties
    layer.set('layer_id', layerId);
    layer.set('layer_type', 'geotiff');
    layer.set('layer_name', config.name || layerId);
    layer.set('title', config.name || layerId);  // For layer switcher
    
    // Get extent from config or source view
  const extent = (sourceView && sourceView.extent) || config.extent || null;
    if (extent) {
      layer.set('extent', extent);
      console.log('GeoTIFF layer extent:', extent);
    }
    
    return {
      layer,
      extent,
      layerId
    };
  } catch (error) {
    console.error('Error creating GeoTIFF layer:', error);
    throw error;
  }
}

/**
 * Parse style configuration and create OpenLayers style.
 * 
 * @param {Object} styleConfig - Style configuration
 * @returns {import('ol/style/Style').default} OpenLayers style
 */
export function createStyleFromConfig(styleConfig) {
  if (!styleConfig) {
    // Default style
    return new Style({
      stroke: new Stroke({
        color: '#3399CC',
        width: 2
      }),
      fill: new Fill({
        color: 'rgba(51, 153, 204, 0.2)'
      }),
      image: new CircleStyle({
        radius: 6,
        fill: new Fill({ color: '#3399CC' }),
        stroke: new Stroke({ color: '#fff', width: 2 })
      })
    });
  }
  
  const styleOptions = {};
  
  // Stroke style
  if (styleConfig.stroke) {
    styleOptions.stroke = new Stroke({
      color: styleConfig.stroke.color || '#3399CC',
      width: styleConfig.stroke.width || 2,
      lineDash: styleConfig.stroke.lineDash
    });
  }
  
  // Fill style
  if (styleConfig.fill) {
    styleOptions.fill = new Fill({
      color: styleConfig.fill.color || 'rgba(51, 153, 204, 0.2)'
    });
  }
  
  // Point/marker style
  if (styleConfig.image) {
    if (styleConfig.image.type === 'circle') {
      styleOptions.image = new CircleStyle({
        radius: styleConfig.image.radius || 6,
        fill: new Fill({
          color: styleConfig.image.fill || '#3399CC'
        }),
        stroke: new Stroke({
          color: styleConfig.image.stroke || '#fff',
          width: styleConfig.image.strokeWidth || 2
        })
      });
    } else if (styleConfig.image.type === 'icon' && styleConfig.image.src) {
      styleOptions.image = new Icon({
        src: styleConfig.image.src,
        scale: styleConfig.image.scale || 1.0,
        anchor: styleConfig.image.anchor || [0.5, 0.5]
      });
    }
  }
  
  // Text style
  if (styleConfig.text) {
    styleOptions.text = new Text({
      text: styleConfig.text.content || '',
      font: styleConfig.text.font || '12px sans-serif',
      fill: new Fill({
        color: styleConfig.text.color || '#000'
      }),
      stroke: new Stroke({
        color: styleConfig.text.strokeColor || '#fff',
        width: styleConfig.text.strokeWidth || 3
      }),
      offsetX: styleConfig.text.offsetX || 0,
      offsetY: styleConfig.text.offsetY || 0
    });
  }
  
  return new Style(styleOptions);
}

/**
 * Create a GeoJSON vector layer.
 * 
 * @param {Object} config - GeoJSON configuration
 * @param {string} config.url - URL to GeoJSON file
 * @param {string} config.layer_id - Unique layer identifier
 * @param {Object} [config.style] - Style configuration
 * @param {number} [config.opacity=1.0] - Layer opacity (0-1)
 * @param {boolean} [config.visible=true] - Initial visibility
 * @param {import('ol/proj/Projection').default} projection - Map projection object
 * @returns {Promise<Object>} Layer and metadata
 */
export async function createGeoJSONLayer(config, projection) {
  try {
    if (!config.url) {
      throw new Error('GeoJSON configuration requires url');
    }
    
    const layerId = config.layer_id || `geojson_${Date.now()}`;
    
    console.log('Creating GeoJSON layer:', layerId, config.url);
    
    // Create vector source
    const source = new VectorSource({
      url: config.url,
      format: new GeoJSON({
        // Will automatically reproject to map projection if needed
        dataProjection: 'IAU_MOON_GEOG',  // Assume geographic by default
        featureProjection: projection
      })
    });
    
    // Create style
    const style = createStyleFromConfig(config.style);
    
    // Create vector layer
    const layer = new VectorLayer({
      source: source,
      style: style,
      opacity: config.opacity !== undefined ? config.opacity : 1.0,
      visible: config.visible !== undefined ? config.visible : true
    });
    
    // Set layer properties
    layer.set('layer_id', layerId);
    layer.set('layer_type', 'geojson');
    layer.set('layer_name', config.name || layerId);
    layer.set('hoverable', true);
    layer.set('clickable', true);
    
    // Get extent after features load
    let extent = null;
    source.on('change', () => {
      if (source.getState() === 'ready') {
        extent = source.getExtent();
        layer.set('extent', extent);
        console.log('GeoJSON extent:', extent, `(${source.getFeatures().length} features)`);
      }
    });
    
    return {
      layer,
      extent,
      layerId
    };
  } catch (error) {
    console.error('Error creating GeoJSON layer:', error);
    throw error;
  }
}

/**
 * Fit map view to layer extent.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {import('ol/layer/Base').default} layer - Layer to fit
 * @param {Object} options - Fit options
 */
export function fitToLayerExtent(map, layer, options = {}) {
  const extent = layer.get('extent');
  
  if (!extent) {
    console.warn('Layer has no extent, cannot fit view');
    return;
  }
  
  const view = map.getView();
  view.fit(extent, {
    padding: options.padding || [50, 50, 50, 50],
    duration: options.duration !== undefined ? options.duration : 1000,
    maxZoom: options.maxZoom
  });
}

/**
 * Find layer by ID.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {string} layerId - Layer ID to find
 * @returns {import('ol/layer/Base').default|null} Layer or null
 */
export function findLayerById(map, layerId) {
  let foundLayer = null;
  
  map.getLayers().forEach(layer => {
    if (layer.get('layer_id') === layerId) {
      foundLayer = layer;
    }
  });
  
  return foundLayer;
}

/**
 * Toggle layer visibility.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {string} layerId - Layer ID
 * @param {boolean} visible - Visibility state
 */
export function toggleLayer(map, layerId, visible) {
  const layer = findLayerById(map, layerId);
  if (layer) {
    layer.setVisible(visible);
    console.log(`Layer ${layerId} visibility:`, visible);
  } else {
    console.warn(`Layer ${layerId} not found`);
  }
}

/**
 * Set layer opacity.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {string} layerId - Layer ID
 * @param {number} opacity - Opacity (0-1)
 */
export function setLayerOpacity(map, layerId, opacity) {
  const layer = findLayerById(map, layerId);
  if (layer) {
    layer.setOpacity(Math.max(0, Math.min(1, opacity)));
    console.log(`Layer ${layerId} opacity:`, opacity);
  } else {
    console.warn(`Layer ${layerId} not found`);
  }
}
