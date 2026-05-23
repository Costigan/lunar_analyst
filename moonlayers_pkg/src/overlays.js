/**
 * Overlays for popups and hover highlights.
 */

import Overlay from 'ol/Overlay';
import { Style, Stroke, Fill, Circle as CircleStyle } from 'ol/style';

/**
 * Create popup overlay for displaying feature information.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @returns {Object} Popup tools
 */
export function createPopup(map) {
  // Create popup container
  const container = document.createElement('div');
  container.className = 'ol-popup';
  container.innerHTML = `
    <a href="#" class="ol-popup-closer"></a>
    <div class="ol-popup-content"></div>
  `;
  
  // Create overlay
  const overlay = new Overlay({
    element: container,
    autoPan: {
      animation: {
        duration: 250
      }
    },
    positioning: 'bottom-center',
    offset: [0, -10]
  });
  
  map.addOverlay(overlay);
  
  // Get elements
  const closer = container.querySelector('.ol-popup-closer');
  const content = container.querySelector('.ol-popup-content');
  
  // Close popup handler
  closer.onclick = function() {
    overlay.setPosition(undefined);
    closer.blur();
    return false;
  };
  
  /**
   * Show popup with feature properties.
   */
  const show = (coordinate, feature) => {
    const properties = feature.getProperties();
    
    // Build HTML content from properties
    let html = '<div class="feature-info">';
    
    // Skip geometry property
    for (const [key, value] of Object.entries(properties)) {
      if (key === 'geometry') continue;
      
      html += `<div class="feature-property">
        <span class="property-key">${key}:</span>
        <span class="property-value">${value}</span>
      </div>`;
    }
    
    html += '</div>';
    
    content.innerHTML = html;
    overlay.setPosition(coordinate);
  };
  
  /**
   * Hide popup.
   */
  const hide = () => {
    overlay.setPosition(undefined);
  };
  
  return {
    overlay,
    container,
    show,
    hide
  };
}

/**
 * Create hover highlight style.
 * 
 * @returns {import('ol/style/Style').default} Hover style
 */
export function createHoverStyle() {
  return new Style({
    stroke: new Stroke({
      color: '#ff3333',
      width: 3
    }),
    fill: new Fill({
      color: 'rgba(255, 51, 51, 0.1)'
    }),
    image: new CircleStyle({
      radius: 8,
      fill: new Fill({
        color: '#ff3333'
      }),
      stroke: new Stroke({
        color: '#fff',
        width: 2
      })
    })
  });
}

/**
 * Setup click and hover interactions for features.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {Object} popup - Popup tools from createPopup
 * @param {Function} onClickCallback - Callback for click events
 * @param {Function} onHoverCallback - Callback for hover events
 * @returns {Object} Interaction tools
 */
export function setupFeatureInteractions(map, popup, onClickCallback, onHoverCallback) {
  const hoverStyle = createHoverStyle();
  let currentFeature = null;
  let currentLayer = null;
  let originalStyle = null;
  
  /**
   * Clear hover highlight.
   */
  const clearHover = () => {
    if (currentFeature && currentLayer) {
      // Restore original style
      if (originalStyle !== null) {
        currentFeature.setStyle(originalStyle);
      } else {
        currentFeature.setStyle(undefined);
      }
      currentFeature = null;
      currentLayer = null;
      originalStyle = null;
    }
  };
  
  /**
   * Handle pointer move (hover).
   */
  map.on('pointermove', (evt) => {
    if (evt.dragging) {
      clearHover();
      return;
    }
    
    const pixel = map.getEventPixel(evt.originalEvent);
    let featureFound = false;
    
    // Check for features at pixel
    map.forEachFeatureAtPixel(pixel, (feature, layer) => {
      // Only interact with vector layers that are marked as hoverable
      if (layer && layer.get('hoverable')) {
        featureFound = true;
        
        // If this is a new feature, update highlight
        if (feature !== currentFeature) {
          clearHover();
          
          currentFeature = feature;
          currentLayer = layer;
          originalStyle = feature.getStyle();
          
          // Apply hover style
          feature.setStyle(hoverStyle);
          
          // Call hover callback
          if (onHoverCallback) {
            const properties = feature.getProperties();
            delete properties.geometry;  // Don't send geometry
            onHoverCallback(properties, evt.coordinate);
          }
        }
        
        // Change cursor
        map.getTargetElement().style.cursor = 'pointer';
        return true;
      }
    });
    
    // No feature found, clear hover
    if (!featureFound) {
      clearHover();
      map.getTargetElement().style.cursor = '';
    }
  });
  
  /**
   * Handle click.
   */
  map.on('click', (evt) => {
    const pixel = map.getEventPixel(evt.originalEvent);
    let featureFound = false;
    
    map.forEachFeatureAtPixel(pixel, (feature, layer) => {
      // Only interact with vector layers that are marked as clickable
      if (layer && layer.get('clickable')) {
        featureFound = true;
        
        // Show popup
        popup.show(evt.coordinate, feature);
        
        // Call click callback
        if (onClickCallback) {
          const properties = feature.getProperties();
          delete properties.geometry;  // Don't send geometry
          onClickCallback(properties, evt.coordinate);
        }
        
        return true;
      }
    });
    
    // No feature found, hide popup
    if (!featureFound) {
      popup.hide();
    }
  });
  
  return {
    clearHover
  };
}
