/**
 * Projection registration for Moon stereographic projections.
 * Handles ESRI:103878 and custom proj4 definitions.
 */

import { register } from 'ol/proj/proj4';
import { get as getProjection, addProjection, addEquivalentProjections } from 'ol/proj';
import Projection from 'ol/proj/Projection';
import proj4 from 'proj4';

// Moon radius in meters
export const MOON_RADIUS = 1737400;

// ESRI:103878 - Moon South Pole Stereographic
const ESRI_103878_PROJ4 = '+proj=stere +lat_0=-90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs';

// Custom Moon South Polar (from spec example)
const CUSTOM_MOON_SOUTH_PROJ4 = '+proj=stere +lat_0=-85.42088 +lon_0=31.6218 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs';

// IAU Moon Geographic (for lat/lon readouts)
const IAU_MOON_GEOG_PROJ4 = '+proj=longlat +a=1737400 +b=1737400 +no_defs';

/**
 * Register a projection with proj4 and OpenLayers.
 * 
 * @param {string} code - Projection code (e.g., 'ESRI:103878')
 * @param {string} proj4def - Proj4 definition string
 * @param {Object} options - Additional projection options
 * @returns {import('ol/proj/Projection').default} The registered projection
 */
export function registerProjection(code, proj4def, options = {}) {
  try {
    // Register with proj4
    proj4.defs(code, proj4def);
    
    // Create a new Projection instance explicitly
    const projection = new Projection({
      code: code,
      units: 'm',
      extent: options.extent,
      worldExtent: options.worldExtent,
      metersPerUnit: options.metersPerUnit !== undefined ? options.metersPerUnit : 1.0,
      global: false
    });
    
    // Add the projection to OpenLayers
    addProjection(projection);
    
    // Register proj4 transformations
    register(proj4);
    
    console.log(`Registered projection ${code}`);
    
    return projection;
  } catch (error) {
    console.error(`Error registering projection ${code}:`, error);
    throw error;
  }
}

/**
 * Initialize standard Moon projections.
 * Registers ESRI:103878, custom Moon South, and IAU Moon Geographic.
 * 
 * @returns {Object} Map of registered projections
 */
export function initializeMoonProjections() {
  const projections = {};
  
  // ESRI:103878 - Moon South Pole Stereographic
  // Extent covers the south polar region
  const southPoleExtent = [-2000000, -2000000, 2000000, 2000000];
  projections['ESRI:103878'] = registerProjection(
    'ESRI:103878',
    ESRI_103878_PROJ4,
    {
      extent: southPoleExtent,
      metersPerUnit: 1.0
    }
  );
  
  // Register URN variant for WMTS compatibility
  // Many WMTS services use URN format instead of ESRI: prefix
  const urnProjection = registerProjection(
    'urn:ogc:def:crs:ESRI::103878',
    ESRI_103878_PROJ4,
    {
      extent: southPoleExtent,
      metersPerUnit: 1.0
    }
  );
  
  // Register Moon Trek's non-standard EPSG::0 code (used in their south polar WMTS)
  // This is a Moon Trek-specific quirk where they use EPSG::0 for their polar projections
  const epsg0Projection = registerProjection(
    'EPSG::0',
    ESRI_103878_PROJ4,
    {
      extent: southPoleExtent,
      metersPerUnit: 1.0
    }
  );
  
  const urnEpsg0Projection = registerProjection(
    'urn:ogc:def:crs:EPSG::0',
    ESRI_103878_PROJ4,
    {
      extent: southPoleExtent,
      metersPerUnit: 1.0
    }
  );
  
  // Mark them all as equivalent so OpenLayers can use them interchangeably
  addEquivalentProjections([
    projections['ESRI:103878'], 
    urnProjection,
    epsg0Projection,
    urnEpsg0Projection
  ]);
  
  // IAU Moon Geographic (for coordinate readouts)
  projections['IAU_MOON_GEOG'] = registerProjection(
    'IAU_MOON_GEOG',
    IAU_MOON_GEOG_PROJ4,
    {
      extent: [-180, -90, 180, 90],
      metersPerUnit: MOON_RADIUS * Math.PI / 180,
      worldExtent: [-180, -90, 180, 90]
    }
  );
  
  return projections;
}

/**
 * Setup projection based on user configuration.
 * 
 * @param {string|Object} projectionConfig - Either a string code or config object
 * @returns {import('ol/proj/Projection').default} The configured projection
 */
export function setupProjection(projectionConfig) {
  // Initialize standard Moon projections
  const registeredProjections = initializeMoonProjections();
  
  // Handle string configuration (use standard projection)
  if (typeof projectionConfig === 'string') {
    const projection = getProjection(projectionConfig);
    if (!projection) {
      throw new Error(`Projection ${projectionConfig} not found. Make sure it's registered.`);
    }
    return projection;
  }
  
  // Handle custom projection configuration
  if (projectionConfig && typeof projectionConfig === 'object') {
    const { code, proj4, extent, worldExtent, metersPerUnit } = projectionConfig;
    
    if (!code || !proj4) {
      throw new Error('Custom projection requires both "code" and "proj4" properties');
    }
    
    return registerProjection(code, proj4, {
      extent,
      worldExtent,
      metersPerUnit
    });
  }
  
  // Default to ESRI:103878
  return registeredProjections['ESRI:103878'];
}

/**
 * Get the IAU Moon Geographic projection for coordinate transformations.
 * 
 * @returns {import('ol/proj/Projection').default} IAU Moon Geographic projection
 */
export function getMoonGeographicProjection() {
  return getProjection('IAU_MOON_GEOG');
}
