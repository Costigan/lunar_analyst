/**
 * Map controls setup and management.
 */

import {
  defaults as defaultControls,
  Zoom,
  ZoomSlider,
  Rotate,
  ScaleLine,
  MousePosition,
  OverviewMap,
  FullScreen,
  ZoomToExtent
} from 'ol/control';
import LayerSwitcher from 'ol-layerswitcher';
import { createStringXY } from 'ol/coordinate';
import { transform } from 'ol/proj';
import { getMoonGeographicProjection } from './projection.js';

/**
 * Create map controls based on configuration.
 * 
 * @param {Object} config - Controls configuration
 * @param {import('ol/Map').default} map - OpenLayers map instance
 * @returns {Object} Controls object with references
 */
export function createControls(config, map) {
  const controls = {};
  const controlsArray = [];
  
  // Zoom control
  if (config.zoom) {
    controls.zoom = new Zoom();
    controlsArray.push(controls.zoom);
  }
  
  // Zoom slider control
  if (config.zoom_slider) {
    controls.zoomSlider = new ZoomSlider();
    controlsArray.push(controls.zoomSlider);
  }
  
  // Rotate control
  if (config.rotate) {
    controls.rotate = new Rotate({
      autoHide: false
    });
    controlsArray.push(controls.rotate);
  }
  
  // Scale line control
  if (config.scale_line) {
    controls.scaleLine = new ScaleLine({
      units: 'metric',
      bar: true,
      steps: 4,
      text: true,
      minWidth: 140
    });
    controlsArray.push(controls.scaleLine);
  }
  
  // Mouse position control
  if (config.mouse_position) {
    const mouseConfig = typeof config.mouse_position === 'object' 
      ? config.mouse_position 
      : {};
    
    const projection = mouseConfig.proj || 'IAU_MOON_GEOG';
    const precision = mouseConfig.precision !== undefined ? mouseConfig.precision : 4;
    
    // Create coordinate formatter
    let coordinateFormat;
    if (projection === 'IAU_MOON_GEOG') {
      // Display as lat/lon
      coordinateFormat = (coord) => {
        if (!coord) return '';
        const mapProjection = map.getView().getProjection();
        const geoProjection = getMoonGeographicProjection();
        
        try {
          const [lon, lat] = transform(coord, mapProjection, geoProjection);
          return `Lat: ${lat.toFixed(precision)}°, Lon: ${lon.toFixed(precision)}°`;
        } catch (error) {
          console.warn('Coordinate transform error:', error);
          return '';
        }
      };
    } else {
      // Display in map projection units
      coordinateFormat = createStringXY(precision);
    }
    
    controls.mousePosition = new MousePosition({
      coordinateFormat: coordinateFormat,
      projection: projection === 'IAU_MOON_GEOG' ? undefined : projection,
      className: 'custom-mouse-position',
      placeholder: 'Mouse position'
    });
    controlsArray.push(controls.mousePosition);
    
    // Style the mouse position element after creation
    setTimeout(() => {
      const element = controls.mousePosition.element;
      if (element) {
        element.style.position = 'absolute';
        element.style.bottom = '8px';
        element.style.right = '48px'; // To the left of fullscreen button
        element.style.left = 'auto';
        element.style.top = 'auto';
        element.style.background = 'rgba(255, 255, 255, 0.9)';
        element.style.padding = '4px 8px';
        element.style.borderRadius = '4px';
        element.style.fontSize = '12px';
        element.style.fontFamily = 'monospace';
        element.style.border = '1px solid rgba(0, 0, 0, 0.2)';
        element.style.zIndex = '100';
      }
    }, 0);
  }
  
  // Overview map control
  if (config.overview_map) {
    controls.overviewMap = new OverviewMap({
      collapsed: true,
      collapsible: true
    });
    controlsArray.push(controls.overviewMap);
  }
  
  // Fullscreen control
  if (config.fullscreen) {
    controls.fullScreen = new FullScreen();
    controlsArray.push(controls.fullScreen);
  }
  
  return {
    controls,
    controlsArray
  };
}

/**
 * Create layer switcher control.
 * 
 * @param {Object} options - Layer switcher options
 * @returns {LayerSwitcher} Layer switcher control
 */
export function createLayerSwitcher(options = {}) {
  const switcher = new LayerSwitcher({
    reverse: options.reverse !== undefined ? options.reverse : true,
    groupSelectStyle: options.groupSelectStyle || 'children',
    activationMode: options.activationMode || 'click',
    startActive: options.startActive !== undefined ? options.startActive : false  // Start collapsed
  });
  
  console.log('[LayerSwitcher] Created layer switcher, startActive:', options.startActive !== undefined ? options.startActive : false);
  
  return switcher;
}

/**
 * Add layer ordering controls to the layer switcher.
 * 
 * @param {LayerSwitcher} layerSwitcher - Layer switcher control
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {Object} model - Anywidget model for syncing state
 * @returns {Function} Enhancement function that can be called manually
 */
export function addLayerOrderingControls(layerSwitcher, map, model) {
  console.log('[LayerOrdering] Setting up layer ordering controls');
  
  // Inject CSS styles for layer ordering controls
  injectLayerOrderingStyles();
  
  // Function to enhance the layer switcher
  const enhance = () => {
    console.log('[LayerOrdering] Enhance function called');
    
    // Run enhancement after a short delay to let panel render
    setTimeout(() => {
      enhanceLayerSwitcherWithOrdering(layerSwitcher, map, model);
    }, 50);
  };
  
  // Create a MutationObserver to watch for layer switcher panel changes
  const observer = new MutationObserver((mutations) => {
    console.log('[LayerOrdering] MutationObserver triggered, mutations:', mutations.length);
    enhance();
  });
  
  // Observe the layer switcher element for changes
  observer.observe(layerSwitcher.element, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style']
  });
  
  // Initial enhancement - try multiple times to ensure it catches the panel rendering
  console.log('[LayerOrdering] Scheduling initial enhancements');
  setTimeout(() => {
    console.log('[LayerOrdering] Running enhancement at 100ms');
    enhance();
  }, 100);
  setTimeout(() => {
    console.log('[LayerOrdering] Running enhancement at 500ms');
    enhance();
  }, 500);
  setTimeout(() => {
    console.log('[LayerOrdering] Running enhancement at 1000ms');
    enhance();
  }, 1000);
  
  // Also enhance whenever the layer switcher button is clicked
  const button = layerSwitcher.element.querySelector('button');
  if (button) {
    console.log('[LayerOrdering] Adding click listener to layer switcher button');
    button.addEventListener('click', () => {
      console.log('[LayerOrdering] Layer switcher button clicked');
      setTimeout(() => {
        console.log('[LayerOrdering] Running enhancement after button click');
        enhance();
      }, 100);
    });
  } else {
    console.warn('[LayerOrdering] Layer switcher button not found');
  }
  
  // Store reference for cleanup
  layerSwitcher.orderingObserver = observer;
  
  // Return the enhance function so it can be called externally
  return enhance;
}

/**
 * Inject CSS styles for layer ordering controls.
 * @private
 */
function injectLayerOrderingStyles() {
  // Check if styles already injected
  if (document.getElementById('moonlayers-layer-ordering-styles')) {
    return;
  }
  
  const styleEl = document.createElement('style');
  styleEl.id = 'moonlayers-layer-ordering-styles';
  styleEl.textContent = `
    /* Layer ordering controls */
    .layer-order-controls {
      display: inline-flex !important;
      gap: 3px !important;
      margin-left: 8px !important;
      vertical-align: middle !important;
      flex-shrink: 0 !important;
    }
    
    .layer-order-btn {
      background: rgba(220, 220, 220, 0.9) !important;
      color: rgba(0, 0, 0, 0.8) !important;
      border: 1px solid rgba(0, 0, 0, 0.2) !important;
      border-radius: 3px !important;
      width: 20px !important;
      height: 20px !important;
      min-width: 20px !important;
      min-height: 20px !important;
      font-size: 12px !important;
      line-height: 1 !important;
      cursor: pointer !important;
      padding: 0 !important;
      margin: 0 !important;
      display: inline-flex !important;
      align-items: center !important;
      justify-content: center !important;
      transition: all 0.15s ease !important;
      flex-shrink: 0 !important;
      box-sizing: border-box !important;
    }
    
    .layer-order-btn:hover {
      background: rgba(180, 180, 180, 0.95) !important;
      color: rgba(0, 0, 0, 1) !important;
      border-color: rgba(0, 0, 0, 0.4) !important;
      transform: scale(1.1) !important;
    }
    
    .layer-order-btn:active {
      transform: scale(0.95) !important;
      background: rgba(160, 160, 160, 0.95) !important;
    }
    
    /* Make sure li.layer can contain the controls */
    .layer-switcher li.layer {
      overflow: visible !important;
      position: relative !important;
    }
    
    .layer-switcher li.layer > label {
      display: inline-block !important;
      max-width: calc(100% - 52px) !important;
      overflow: hidden !important;
      text-overflow: ellipsis !important;
      white-space: nowrap !important;
    }
  `;
  
  document.head.appendChild(styleEl);
  console.log('[LayerOrdering] Styles injected into document head');
}

/**
 * Enhance layer switcher with ordering controls.
 * @private
 */
function enhanceLayerSwitcherWithOrdering(layerSwitcher, map, model) {
  console.log('[LayerOrdering] Enhancement triggered');
  console.log('[LayerOrdering] Layer switcher element:', layerSwitcher.element);
  
  // Check if layer switcher panel exists and is visible
  const panel = layerSwitcher.element.querySelector('.panel');
  console.log('[LayerOrdering] Panel element:', panel);
  
  if (!panel) {
    console.warn('[LayerOrdering] Panel not found, skipping enhancement');
    return;
  }
  
  const panelDisplay = window.getComputedStyle(panel).display;
  console.log('[LayerOrdering] Panel display style:', panelDisplay);
  
  if (panelDisplay === 'none') {
    console.warn('[LayerOrdering] Panel is hidden, skipping enhancement');
    return;
  }
  
  // Find all layer elements in the switcher
  const layerElements = layerSwitcher.element.querySelectorAll('li.layer');
  
  console.log(`[LayerOrdering] Found ${layerElements.length} layer elements`);
  
  layerElements.forEach((li, idx) => {
    // Skip if already enhanced
    if (li.querySelector('.layer-order-controls')) {
      console.log(`[LayerOrdering] Layer ${idx} already has controls`);
      return;
    }
    
    // Get the layer from the label
    const label = li.querySelector('label');
    if (!label) {
      console.warn(`[LayerOrdering] Layer ${idx} has no label, skipping`);
      return;
    }
    
    const layerTitle = label.textContent.trim();
    console.log(`[LayerOrdering] Processing layer ${idx}: "${layerTitle}"`);
    
    const layer = findLayerByTitle(map, layerTitle);
    
    if (!layer) {
      console.warn(`[LayerOrdering] Could not find layer in map: "${layerTitle}"`);
      return;
    }
    
    // All layers can be reordered (including base layer)
    const layerType = layer.get('layer_type');
    const layerId = layer.get('layer_id');
    console.log(`[LayerOrdering] Layer type: ${layerType}, id: ${layerId}`);
    
    console.log(`[LayerOrdering] Adding controls to layer: ${layerTitle}`);
    
    // Create controls container
    const controlsDiv = document.createElement('div');
    controlsDiv.className = 'layer-order-controls';
    // Apply inline styles to ensure visibility
    controlsDiv.style.cssText = `
      display: inline-flex !important;
      gap: 3px !important;
      margin-left: 8px !important;
      vertical-align: middle !important;
      flex-shrink: 0 !important;
    `;
    
    // Create up button
    const upBtn = document.createElement('button');
    upBtn.className = 'layer-order-btn layer-order-up';
    upBtn.innerHTML = '↑';
    upBtn.title = 'Move layer up';
    // Apply inline styles to ensure visibility
    upBtn.style.cssText = `
      background: rgba(220, 220, 220, 0.9) !important;
      color: rgba(0, 0, 0, 0.8) !important;
      border: 1px solid rgba(0, 0, 0, 0.2) !important;
      border-radius: 3px !important;
      width: 20px !important;
      height: 20px !important;
      min-width: 20px !important;
      min-height: 20px !important;
      font-size: 12px !important;
      line-height: 1 !important;
      cursor: pointer !important;
      padding: 0 !important;
      margin: 0 !important;
      display: inline-flex !important;
      align-items: center !important;
      justify-content: center !important;
      transition: all 0.15s ease !important;
      flex-shrink: 0 !important;
      box-sizing: border-box !important;
    `;
    
    // Create down button
    const downBtn = document.createElement('button');
    downBtn.className = 'layer-order-btn layer-order-down';
    downBtn.innerHTML = '↓';
    downBtn.title = 'Move layer down';
    // Apply inline styles to ensure visibility
    downBtn.style.cssText = `
      background: rgba(220, 220, 220, 0.9) !important;
      color: rgba(0, 0, 0, 0.8) !important;
      border: 1px solid rgba(0, 0, 0, 0.2) !important;
      border-radius: 3px !important;
      width: 20px !important;
      height: 20px !important;
      min-width: 20px !important;
      min-height: 20px !important;
      font-size: 12px !important;
      line-height: 1 !important;
      cursor: pointer !important;
      padding: 0 !important;
      margin: 0 !important;
      display: inline-flex !important;
      align-items: center !important;
      justify-content: center !important;
      transition: all 0.15s ease !important;
      flex-shrink: 0 !important;
      box-sizing: border-box !important;
    `;
    
    upBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      console.log('[LayerOrdering] Up button clicked for:', layerTitle);
      moveLayerUp(map, layer, model, layerSwitcher);
      // Re-enhance after reordering
      setTimeout(() => enhanceLayerSwitcherWithOrdering(layerSwitcher, map, model), 100);
    };
    
    downBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      console.log('[LayerOrdering] Down button clicked for:', layerTitle);
      moveLayerDown(map, layer, model, layerSwitcher);
      // Re-enhance after reordering
      setTimeout(() => enhanceLayerSwitcherWithOrdering(layerSwitcher, map, model), 100);
    };
    
    controlsDiv.appendChild(upBtn);
    controlsDiv.appendChild(downBtn);
    
    // Insert controls after the label
    li.appendChild(controlsDiv);
    console.log(`[LayerOrdering] Controls appended to layer ${idx}`);
    console.log('[LayerOrdering] Controls element:', controlsDiv);
    console.log('[LayerOrdering] Controls bounding rect:', controlsDiv.getBoundingClientRect());
  });
  
  console.log('[LayerOrdering] Enhancement complete');
}

/**
 * Find a layer by its title.
 * @private
 */
function findLayerByTitle(map, title) {
  const layers = map.getLayers().getArray();
  return layers.find(l => {
    const layerTitle = l.get('title') || l.get('layer_name') || '';
    return layerTitle === title;
  });
}

/**
 * Move a layer up in the layer stack (towards top/front).
 */
function moveLayerUp(map, layer, model, layerSwitcher) {
  const layers = map.getLayers();
  const index = layers.getArray().indexOf(layer);
  
  // Can't move up if already at top
  if (index >= layers.getLength() - 1) {
    console.log('[LayerOrdering] Layer already at top');
    return;
  }
  
  // Remove and re-insert at higher position
  layers.removeAt(index);
  layers.insertAt(index + 1, layer);
  
  console.log('[LayerOrdering] Moved layer up:', layer.get('layer_name') || layer.get('title'));
  
  // Force layer switcher to re-render
  if (layerSwitcher && typeof layerSwitcher.renderPanel === 'function') {
    layerSwitcher.renderPanel();
    console.log('[LayerOrdering] Layer switcher panel re-rendered');
  }
  
  // Sync with Python model
  syncLayerOrder(map, model);
  
  // Force map render
  map.render();
}

/**
 * Move a layer down in the layer stack (towards bottom/back).
 */
function moveLayerDown(map, layer, model, layerSwitcher) {
  const layers = map.getLayers();
  const index = layers.getArray().indexOf(layer);
  
  // Can't move below bottom (index 0)
  if (index <= 0) {
    console.log('[LayerOrdering] Layer already at bottom');
    return;
  }
  
  // Remove and re-insert at lower position
  layers.removeAt(index);
  layers.insertAt(index - 1, layer);
  
  console.log('[LayerOrdering] Moved layer down:', layer.get('layer_name') || layer.get('title'));
  
  // Force layer switcher to re-render
  if (layerSwitcher && typeof layerSwitcher.renderPanel === 'function') {
    layerSwitcher.renderPanel();
    console.log('[LayerOrdering] Layer switcher panel re-rendered');
  }
  
  // Sync with Python model
  syncLayerOrder(map, model);
  
  // Force map render
  map.render();
}

/**
 * Sync layer order with Python model.
 * @private
 */
function syncLayerOrder(map, model) {
  const layers = map.getLayers().getArray();
  
  // Get ordered list of ALL layer IDs (including base layer)
  const orderedIds = layers
    .filter(l => l.get('layer_id')) // Any layer with an ID
    .map(l => l.get('layer_id'));
  
  console.log('[LayerOrdering] Syncing layer order to Python:', orderedIds);
  
  model.set('active_layers', orderedIds);
  model.save_changes();
}

/**
 * Restore layer order from active_layers array.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {Array<string>} orderedLayerIds - Array of layer IDs in order
 */
export function restoreLayerOrder(map, orderedLayerIds) {
  const layers = map.getLayers();
  const currentLayers = layers.getArray().slice();
  
  // Get all layers that have IDs
  const layersById = new Map();
  currentLayers.forEach(l => {
    const id = l.get('layer_id');
    if (id) {
      layersById.set(id, l);
    }
  });
  
  // Clear and rebuild layer stack in the specified order
  layers.clear();
  
  orderedLayerIds.forEach(id => {
    const layer = layersById.get(id);
    if (layer) {
      layers.push(layer);
    }
  });
  
  console.log('[LayerOrdering] Restored layer order:', orderedLayerIds);
}

/**
 * Create zoom to extent control for a specific extent.
 * 
 * @param {Array<number>} extent - Extent to zoom to [minX, minY, maxX, maxY]
 * @param {Object} options - Control options
 * @returns {ZoomToExtent} Zoom to extent control
 */
export function createZoomToExtentControl(extent, options = {}) {
  return new ZoomToExtent({
    extent: extent,
    label: options.label || '⌂',
    tipLabel: options.tipLabel || 'Zoom to extent'
  });
}

/**
 * Toggle control visibility.
 * 
 * @param {import('ol/control/Control').default} control - Control to toggle
 * @param {boolean} visible - Visibility state
 */
export function toggleControl(control, visible) {
  if (!control) return;
  
  const element = control.element;
  if (element) {
    element.style.display = visible ? '' : 'none';
  }
}

/**
 * Update controls based on configuration changes.
 * 
 * @param {import('ol/Map').default} map - OpenLayers map
 * @param {Object} controlsRef - Reference to controls object
 * @param {Object} newConfig - New controls configuration
 */
export function updateControls(map, controlsRef, newConfig) {
  // Toggle individual controls
  if (controlsRef.zoom) {
    toggleControl(controlsRef.zoom, newConfig.zoom);
  }
  if (controlsRef.zoomSlider) {
    toggleControl(controlsRef.zoomSlider, newConfig.zoom_slider);
  }
  if (controlsRef.rotate) {
    toggleControl(controlsRef.rotate, newConfig.rotate);
  }
  if (controlsRef.scaleLine) {
    toggleControl(controlsRef.scaleLine, newConfig.scale_line);
  }
  if (controlsRef.mousePosition) {
    toggleControl(controlsRef.mousePosition, newConfig.mouse_position);
  }
  if (controlsRef.overviewMap) {
    toggleControl(controlsRef.overviewMap, newConfig.overview_map);
  }
  if (controlsRef.fullScreen) {
    toggleControl(controlsRef.fullScreen, newConfig.fullscreen);
  }
}
