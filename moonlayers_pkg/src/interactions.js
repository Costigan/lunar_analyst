/**
 * Map interactions including measurement and permalink.
 */

import { defaults as defaultInteractions, DragRotateAndZoom, Draw, Modify, Snap } from 'ol/interaction';
// Removed invalid Sphere import; use getLength/getArea with MOON_RADIUS option
import { getLength, getArea } from 'ol/sphere';
import VectorSource from 'ol/source/Vector';
import VectorLayer from 'ol/layer/Vector';
import { Style, Stroke, Fill, Circle as CircleStyle } from 'ol/style';
import { LineString, Polygon } from 'ol/geom';
import Overlay from 'ol/Overlay';
import { MOON_RADIUS } from './projection.js';
import { unByKey } from 'ol/Observable';

// Use MOON_RADIUS with getLength/getArea options

/**
 * Create default map interactions.
 * 
 * @param {Object} options - Interaction options
 * @returns {Array} Array of interactions
 */
export function createDefaultInteractions(options = {}) {
  const interactions = defaultInteractions({
    altShiftDragRotate: true,
    doubleClickZoom: true,
    keyboard: true,
    mouseWheelZoom: true,
    shiftDragZoom: true,
    dragPan: true,
    pinchRotate: true,
    pinchZoom: true
  });
  
  // Add DragRotateAndZoom if requested
  if (options.dragRotateAndZoom) {
    interactions.push(new DragRotateAndZoom());
  }
  
  return interactions;
}

/**
 * Format length measurement.
 * 
 * @param {number} length - Length in meters
 * @returns {string} Formatted length
 */
function formatLength(length) {
  if (length > 1000) {
    return `${(length / 1000).toFixed(2)} km`;
  }
  return `${length.toFixed(2)} m`;
}

/**
 * Format area measurement.
 * 
 * @param {number} area - Area in square meters
 * @returns {string} Formatted area
 */
function formatArea(area) {
  if (area > 1000000) {
    return `${(area / 1000000).toFixed(2)} km²`;
  }
  return `${area.toFixed(2)} m²`;
}

/**
 * Create measurement layer and interactions.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {Object} config - Measurement configuration
 * @returns {Object} Measurement tools
 */
export function createMeasurementTools(map, config = {}) {
  const mode = config.mode || 'geodesic';
  
  // Create vector source and layer for measurements
  const measureSource = new VectorSource();
  const measureLayer = new VectorLayer({
    source: measureSource,
    style: new Style({
      fill: new Fill({
        color: 'rgba(255, 255, 255, 0.2)'
      }),
      stroke: new Stroke({
        color: '#ffcc33',
        width: 3,
        lineDash: [10, 10]
      }),
      image: new CircleStyle({
        radius: 7,
        fill: new Fill({
          color: '#ffcc33'
        })
      })
    })
  });
  
  measureLayer.set('layer_id', 'measurement_layer');
  measureLayer.set('layer_type', 'measurement');
  map.addLayer(measureLayer);
  
  let draw = null;
  let modify = null;
  let snap = null;
  let measureTooltipElement = null;
  let measureTooltip = null;
  let sketch = null;
  let listener = null;
  
  /**
   * Calculate measurement based on geometry and mode.
   */
  const calculateMeasurement = (geom) => {
    if (mode === 'geodesic') {
      if (geom.getType() === 'LineString') {
        return getLength(geom, { projection: map.getView().getProjection(), radius: MOON_RADIUS });
      } else if (geom.getType() === 'Polygon') {
        return getArea(geom, { projection: map.getView().getProjection(), radius: MOON_RADIUS });
      }
    } else {
      // Planar measurement
      if (geom.getType() === 'LineString') {
        return geom.getLength();
      } else if (geom.getType() === 'Polygon') {
        return geom.getArea();
      }
    }
    return 0;
  };
  
  /**
   * Start measuring (length or area).
   */
  const startMeasurement = (type = 'LineString') => {
    // Remove previous draw interaction
    if (draw) {
      map.removeInteraction(draw);
    }
    
    // Create new draw interaction
    draw = new Draw({
      source: measureSource,
      type: type,
      style: new Style({
        fill: new Fill({
          color: 'rgba(255, 255, 255, 0.2)'
        }),
        stroke: new Stroke({
          color: 'rgba(255, 204, 51, 0.8)',
          lineDash: [10, 10],
          width: 3
        }),
        image: new CircleStyle({
          radius: 5,
          stroke: new Stroke({
            color: 'rgba(255, 204, 51, 0.8)'
          }),
          fill: new Fill({
            color: 'rgba(255, 255, 255, 0.4)'
          })
        })
      })
    });
    
    map.addInteraction(draw);
    
    // Create tooltip
    createMeasureTooltip();
    
    draw.on('drawstart', (evt) => {
      sketch = evt.feature;
      
      listener = sketch.getGeometry().on('change', (evt) => {
        const geom = evt.target;
        let output;
        
        if (geom.getType() === 'LineString') {
          output = formatLength(calculateMeasurement(geom));
        } else if (geom.getType() === 'Polygon') {
          output = formatArea(calculateMeasurement(geom));
        }
        
        if (measureTooltipElement) {
          measureTooltipElement.innerHTML = output;
          const coords = geom.getLastCoordinate();
          if (measureTooltip && coords) {
            measureTooltip.setPosition(coords);
          }
        }
      });
    });
    
    draw.on('drawend', (evt) => {
      if (measureTooltipElement) {
        measureTooltipElement.className = 'ol-tooltip ol-tooltip-static';
      }
      measureTooltip.setOffset([0, -7]);
      sketch = null;
      measureTooltipElement = null;
      createMeasureTooltip();
      unByKey(listener);
      
      // Emit measurement to Python
      const feature = evt.feature;
      const geom = feature.getGeometry();
      const measurement = calculateMeasurement(geom);
      
      return {
        type: geom.getType(),
        measurement: measurement,
        formatted: geom.getType() === 'LineString' ? formatLength(measurement) : formatArea(measurement),
        mode: mode
      };
    });
    
    // Add modify and snap interactions
    modify = new Modify({ source: measureSource });
    map.addInteraction(modify);
    
    snap = new Snap({ source: measureSource });
    map.addInteraction(snap);
  };
  
  /**
   * Create measurement tooltip.
   */
  const createMeasureTooltip = () => {
    if (measureTooltipElement) {
      measureTooltipElement.parentNode?.removeChild(measureTooltipElement);
    }
    measureTooltipElement = document.createElement('div');
    measureTooltipElement.className = 'ol-tooltip ol-tooltip-measure';
    measureTooltip = new Overlay({
      element: measureTooltipElement,
      offset: [0, -15],
      positioning: 'bottom-center',
      stopEvent: false,
      insertFirst: false
    });
    map.addOverlay(measureTooltip);
  };
  
  /**
   * Stop measuring.
   */
  const stopMeasurement = () => {
    if (draw) {
      map.removeInteraction(draw);
      draw = null;
    }
    if (modify) {
      map.removeInteraction(modify);
      modify = null;
    }
    if (snap) {
      map.removeInteraction(snap);
      snap = null;
    }
  };
  
  /**
   * Clear all measurements.
   */
  const clearMeasurements = () => {
    measureSource.clear();
    if (measureTooltipElement) {
      measureTooltipElement.parentNode?.removeChild(measureTooltipElement);
      measureTooltipElement = null;
    }
  };
  
  return {
    layer: measureLayer,
    source: measureSource,
    startMeasurement,
    stopMeasurement,
    clearMeasurements,
    enabled: config.enabled || false
  };
}

/**
 * Create permalink functionality.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @returns {Object} Permalink tools
 */
export function createPermalink(map) {
  let shouldUpdate = true;
  
  /**
   * Update URL hash with current view state.
   */
  const updatePermalink = () => {
    if (!shouldUpdate) return;
    
    const view = map.getView();
    const center = view.getCenter();
    const zoom = view.getZoom();
    const rotation = view.getRotation();
    
    const hash = `#map=${zoom.toFixed(2)}/${center[0].toFixed(0)}/${center[1].toFixed(0)}/${rotation.toFixed(4)}`;
    
    // Update URL without triggering navigation
    if (window.history.replaceState) {
      window.history.replaceState(null, '', hash);
    } else {
      window.location.hash = hash;
    }
  };
  
  /**
   * Restore view from URL hash.
   */
  const restoreFromPermalink = () => {
    const hash = window.location.hash.replace('#map=', '');
    if (!hash) return false;
    
    const parts = hash.split('/');
    if (parts.length !== 4) return false;
    
    const zoom = parseFloat(parts[0]);
    const centerX = parseFloat(parts[1]);
    const centerY = parseFloat(parts[2]);
    const rotation = parseFloat(parts[3]);
    
    if (isNaN(zoom) || isNaN(centerX) || isNaN(centerY) || isNaN(rotation)) {
      return false;
    }
    
    const view = map.getView();
    shouldUpdate = false;
    view.setCenter([centerX, centerY]);
    view.setZoom(zoom);
    view.setRotation(rotation);
    shouldUpdate = true;
    
    return true;
  };
  
  /**
   * Get current state as object.
   */
  const getState = () => {
    const view = map.getView();
    return {
      center: view.getCenter(),
      zoom: view.getZoom(),
      rotation: view.getRotation()
    };
  };
  
  // Listen to view changes
  map.on('moveend', updatePermalink);
  
  return {
    updatePermalink,
    restoreFromPermalink,
    getState
  };
}
